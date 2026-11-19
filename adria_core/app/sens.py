"""Recherche par le sens (lot S1, 19/11/2026) — l'équivalent de pgvector, dans la base SQLite du Core.

- Chaque ligne de la mémoire (sauf l'historique brut des lumières/présences, trop bruité) reçoit un
  « vecteur de sens » calculé EN LOCAL par Ollama (modèle d'embeddings, 0 token cloud).
- La recherche par mots (P1) reste la première : la recherche par le sens ne fait que COMPLÉTER quand
  les mots ne trouvent rien ou pas assez (« le contrôle de la voiture » retrouve « CT du 5008 »).
- Règle P1 conservée : rien au hasard. Une ligne n'est rendue que si sa ressemblance dépasse le seuil.
- Rien n'est effacé : un vecteur est seulement ajouté (table `vecteurs`, une ligne par ligne mémoire).
- Si Ollama est indisponible : la recherche par mots fonctionne comme avant.
Interrupteur : input_boolean.adria_core_sens.
"""

import logging
import math
import struct
import time

LOG = logging.getLogger("adria_core.sens")

INTERRUPTEUR = "input_boolean.adria_core_sens"
MODELE = "bge-m3"            # multilingue, très bon en français, ~1,2 Go
SEUIL = 0.55                 # ressemblance minimale (cosinus) pour rendre une ligne
ECART = 0.12                 # on ne garde que les lignes proches de la meilleure
SOURCES_EXCLUES = ("historique",)
LOT = 32
MAX_CAR = 600

try:  # numpy si présent dans l'image (plus rapide) ; sinon Python pur
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None


def emballer(v: list[float]) -> bytes:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return struct.pack(f"{len(v)}f", *(x / n for x in v))


def deballer(b: bytes) -> tuple[float, ...]:
    return struct.unpack(f"{len(b) // 4}f", b)


def texte_ligne(resume: str, detail: str | None) -> str:
    t = resume if not detail or detail.startswith(("light.", "person.", "sensor.")) else f"{resume}. {detail}"
    return t[:MAX_CAR]


class Sens:
    def __init__(self, memoire, ollama_url: str, modele: str = MODELE, seuil: float = SEUIL):
        self.m = memoire
        self.url = ollama_url
        self.modele = modele
        self.seuil = seuil
        self.session = None
        self.indexation: dict = {}
        self._cache: tuple | None = None   # (nb, ids, matrice) gardé en mémoire
        self.m.db.execute(
            "CREATE TABLE IF NOT EXISTS vecteurs (ligne_id INTEGER PRIMARY KEY, modele TEXT NOT NULL,"
            " v BLOB NOT NULL)")
        self.m.db.commit()

    # ---------------------------------------------------------------- Ollama
    async def vecteurs(self, textes: list[str], delai: float = 120) -> list[list[float]]:
        import aiohttp
        async with self.session.post(
                f"{self.url}/api/embed",
                json={"model": self.modele, "input": textes, "keep_alive": "24h"},
                timeout=aiohttp.ClientTimeout(total=delai)) as r:
            if r.status == 404:
                raise ModeleAbsent(self.modele)
            r.raise_for_status()
            return (await r.json()).get("embeddings") or []

    async def installer_modele(self) -> bool:
        """Télécharge le modèle d'embeddings dans Ollama s'il manque (une seule fois)."""
        import aiohttp
        try:
            async with self.session.post(f"{self.url}/api/show", json={"model": self.modele},
                                         timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    return True
            LOG.info("Téléchargement du modèle %s dans Ollama…", self.modele)
            async with self.session.post(f"{self.url}/api/pull", json={"model": self.modele, "stream": False},
                                         timeout=aiohttp.ClientTimeout(total=3600)) as r:
                r.raise_for_status()
                return (await r.json()).get("status") == "success"
        except Exception as e:
            LOG.warning("Modèle %s non installé : %s", self.modele, str(e)[:150])
            return False

    # ---------------------------------------------------------------- indexation
    def a_indexer(self, n: int) -> list[tuple[int, str]]:
        place = ",".join("?" * len(SOURCES_EXCLUES))
        rows = self.m.db.execute(
            "SELECT l.id, l.resume, l.detail FROM lignes l LEFT JOIN vecteurs v ON v.ligne_id = l.id"
            f" WHERE (v.ligne_id IS NULL OR v.modele != ?) AND l.source NOT IN ({place})"
            " ORDER BY l.id DESC LIMIT ?", (self.modele, *SOURCES_EXCLUES, n)).fetchall()
        return [(r[0], texte_ligne(r[1], r[2])) for r in rows]

    async def indexer(self, max_lignes: int = 20000, pause: float = 0.2) -> dict:
        """Calcule les vecteurs manquants (les plus récents d'abord), par lots."""
        t0 = time.monotonic()
        faites, erreur = 0, None
        while faites < max_lignes:
            lot = self.a_indexer(min(LOT, max_lignes - faites))
            if not lot:
                break
            try:
                vs = await self.vecteurs([t for _, t in lot])
            except Exception as e:
                erreur = f"{type(e).__name__}: {str(e)[:120]}"
                break
            if len(vs) != len(lot):
                erreur = "réponse Ollama incomplète"
                break
            self.m.db.executemany("INSERT OR REPLACE INTO vecteurs (ligne_id, modele, v) VALUES (?, ?, ?)",
                                  [(i, self.modele, emballer(v)) for (i, _), v in zip(lot, vs)])
            self.m.db.commit()
            faites += len(lot)
            await _dormir(pause)
        self._cache = None
        self.indexation = {"quand": time.strftime("%Y-%m-%dT%H:%M:%S"), "ajoutes": faites,
                           "duree_s": round(time.monotonic() - t0, 1), "erreur": erreur, **self.stats()}
        LOG.info("Sens : %s", self.indexation)
        return self.indexation

    def stats(self) -> dict:
        place = ",".join("?" * len(SOURCES_EXCLUES))
        total = self.m.db.execute(f"SELECT COUNT(*) FROM lignes WHERE source NOT IN ({place})",
                                  SOURCES_EXCLUES).fetchone()[0]
        faits = self.m.db.execute("SELECT COUNT(*) FROM vecteurs WHERE modele = ?", (self.modele,)).fetchone()[0]
        return {"indexees": faits, "a_indexer": max(0, total - faits), "modele": self.modele}

    # ---------------------------------------------------------------- recherche
    def _matrice(self):
        nb = self.m.db.execute("SELECT COUNT(*) FROM vecteurs WHERE modele = ?", (self.modele,)).fetchone()[0]
        if self._cache and self._cache[0] == nb:
            return self._cache[1], self._cache[2]
        rows = self.m.db.execute("SELECT ligne_id, v FROM vecteurs WHERE modele = ?", (self.modele,)).fetchall()
        ids = [r[0] for r in rows]
        if np is not None and rows:
            mat = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
        else:
            mat = [deballer(r[1]) for r in rows]
        self._cache = (nb, ids, mat)
        return ids, mat

    def classer(self, q: list[float], n: int) -> list[tuple[int, float]]:
        ids, mat = self._matrice()
        if not ids:
            return []
        qn = deballer(emballer(q))
        if np is not None and not isinstance(mat, list):
            scores = mat @ np.asarray(qn, dtype=np.float32)
            idx = np.argsort(-scores)[: n * 4]
            paires = [(ids[i], float(scores[i])) for i in idx]
        else:
            paires = sorted(((i, sum(a * b for a, b in zip(v, qn))) for i, v in zip(ids, mat)),
                            key=lambda x: -x[1])[: n * 4]
        if not paires or paires[0][1] < self.seuil:
            return []
        haut = paires[0][1]
        return [(i, s) for i, s in paires if s >= self.seuil and s >= haut - ECART]

    async def chercher(self, question: str, n: int = 5, exclure: list[str] | None = None,
                       delai: float = 10) -> list[dict]:
        vs = await self.vecteurs([question[:MAX_CAR]], delai=delai)
        if not vs:
            return []
        from store import cle_doublon
        res, vus = [], set()
        for ligne_id, score in self.classer(vs[0], n):
            r = self.m.db.execute("SELECT source, resume, detail, date_evt FROM lignes WHERE id = ?",
                                  (ligne_id,)).fetchone()
            if not r or r["source"] in (exclure or []):
                continue
            cle = cle_doublon(r["resume"])
            if cle in vus:
                continue
            vus.add(cle)
            res.append({"source": r["source"], "date": r["date_evt"] or "", "texte": r["resume"],
                        "detail": r["detail"], "par": "sens", "score": round(score, 3)})
            if len(res) >= n:
                break
        return res


class ModeleAbsent(RuntimeError):
    pass


async def _dormir(s: float) -> None:
    import asyncio
    await asyncio.sleep(s)


def fusionner(par_mots: list[dict], par_sens: list[dict], n: int) -> list[dict]:
    """Mots d'abord (P1), puis le sens pour compléter, sans doublon."""
    from store import cle_doublon
    vus = {cle_doublon(l["texte"]) for l in par_mots}
    res = list(par_mots)
    for l in par_sens:
        if len(res) >= n:
            break
        if cle_doublon(l["texte"]) not in vus:
            vus.add(cle_doublon(l["texte"]))
            res.append(l)
    return res
