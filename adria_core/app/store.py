"""Mémoire longue d'ADRIA : stockage SQLite + recherche plein texte (FTS5).

Rien n'est jamais effacé : une ligne déjà vue met seulement à jour `last_seen`.
"""

import hashlib
import re
import sqlite3
import unicodedata
from datetime import datetime

MOTS_VIDES = {
    "les", "des", "une", "est", "que", "qui", "quoi", "pour", "avec", "dans", "sur",
    "par", "pas", "quand", "quel", "quelle", "quels", "quelles", "comment", "combien",
    "pourquoi", "peux", "peut", "faire", "fait", "sont", "etre", "avoir", "cette",
    "cela", "adria", "alexa", "dois", "faut", "mais", "plus", "tout", "tous", "toute",
    "leur", "elle", "ils", "nous", "vous", "sans", "chez", "entre", "aussi", "alors",
    "donc", "mon", "ton", "son", "mes", "tes", "ses", "moi", "toi", "aux", "ont",
    "ete", "dis", "donne", "sais", "sait", "avait", "avais", "avions", "deja",
    "souviens", "rappelle", "rappelles", "hier", "jour", "fois", "raconte", "racontes",
    "passe", "passee", "passes", "derniere", "dernier", "semaine", "mois",
}


def normaliser(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", texte or "")
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return texte.lower()


def mots_cles(question: str) -> list[str]:
    mots = re.findall(r"[a-z0-9]{3,}", normaliser(question))
    vus: list[str] = []
    for m in mots:
        if m in MOTS_VIDES:
            continue
        racine = m[:5]
        if racine not in vus:
            vus.append(racine)
    return vus


class Memoire:
    def __init__(self, chemin: str):
        self.db = sqlite3.connect(chemin, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS lignes (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                uid TEXT,
                resume TEXT NOT NULL,
                detail TEXT,
                empreinte TEXT NOT NULL UNIQUE,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                date_evt TEXT
            );
            CREATE TABLE IF NOT EXISTS meta (cle TEXT PRIMARY KEY, valeur TEXT);
            CREATE TABLE IF NOT EXISTS ia_stats (
                quand REAL NOT NULL, moteur TEXT NOT NULL, profondeur TEXT,
                ok INTEGER NOT NULL, duree REAL, raison TEXT
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS lignes_fts USING fts5(
                resume, detail, tokenize = 'unicode61 remove_diacritics 2'
            );
            """
        )
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(lignes)")]
        if "date_evt" not in cols:  # base v0.1.0
            self.db.execute("ALTER TABLE lignes ADD COLUMN date_evt TEXT")
        self.db.commit()

    def meta(self, cle: str, valeur: str | None = None) -> str | None:
        if valeur is not None:
            self.db.execute("INSERT OR REPLACE INTO meta (cle, valeur) VALUES (?, ?)", (cle, valeur))
            self.db.commit()
            return valeur
        r = self.db.execute("SELECT valeur FROM meta WHERE cle = ?", (cle,)).fetchone()
        return r[0] if r else None

    def ajouter(self, source: str, items: list[dict], maintenant: datetime | None = None) -> int:
        """Ajoute les lignes nouvelles. Renvoie le nombre de lignes nouvelles."""
        quand = (maintenant or datetime.now()).isoformat(timespec="seconds")
        nouvelles = 0
        for it in items:
            resume = (it.get("summary") or "").strip()
            if not resume:
                continue
            detail = (it.get("description") or "").strip()
            empreinte = hashlib.sha1(f"{source}\x1f{resume}\x1f{detail}".encode()).hexdigest()
            cur = self.db.execute(
                "UPDATE lignes SET last_seen = ? WHERE empreinte = ?", (quand, empreinte)
            )
            if cur.rowcount:
                continue
            cur = self.db.execute(
                "INSERT INTO lignes (source, uid, resume, detail, empreinte, first_seen, last_seen, date_evt)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (source, it.get("uid"), resume, detail, empreinte, quand, quand, it.get("date")),
            )
            self.db.execute(
                "INSERT INTO lignes_fts (rowid, resume, detail) VALUES (?, ?, ?)",
                (cur.lastrowid, resume, detail),
            )
            nouvelles += 1
        self.db.commit()
        return nouvelles

    # Ordre de priorité pour raconter une période : d'abord ce qui résume, ensuite le détail.
    PRIORITE_PERIODE = [
        "archive/souvenir", "adria_souvenirs", "archive/episode", "adria_jarvis_episodes",
        "archive/journal_interieur", "adria_journal_interieur", "archive/journal",
        "adria_journal", "archive/temoin", "archive/memoire_perso", "adria_memoire_perso",
        "historique", "archive/observation", "adria_observations",
    ]

    def chercher_periode(self, debut: str, fin: str, mots: list[str], n: int,
                         exclure: list[str]) -> list[dict]:
        """Lignes datées entre debut et fin (AAAA-MM-JJ), filtrées par mots s'il y en a."""
        filtre = ""
        if exclure:
            filtre = " AND l.source NOT IN (%s)" % ",".join("?" * len(exclure))
        rang = " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(self.PRIORITE_PERIODE))
        ordre = f" ORDER BY CASE l.source {rang} ELSE 99 END, l.date_evt, l.id LIMIT ?"
        base = ("SELECT l.source, l.resume, l.detail, l.date_evt FROM lignes l"
                " WHERE l.date_evt BETWEEN ? AND ?" + filtre)
        params = [debut, fin, *exclure]
        lignes = []
        if mots:
            requete = " OR ".join(f'"{m}"*' for m in mots)
            lignes = self.db.execute(
                base.replace("FROM lignes l", "FROM lignes_fts JOIN lignes l ON l.id = lignes_fts.rowid")
                + " AND lignes_fts MATCH ?" + ordre, (*params, requete, n * 3)).fetchall()
        if not lignes:  # pas de mot ou rien trouvé : toute la période, par priorité
            lignes = self.db.execute(base + ordre, (*params, n * 3)).fetchall()
        vus: set[str] = set()
        res: list[dict] = []
        for r in lignes:
            cle = normaliser(r["resume"])[:120]
            if cle in vus:
                continue
            vus.add(cle)
            res.append({"source": r["source"], "date": r["date_evt"] or "",
                        "texte": r["resume"], "detail": r["detail"]})
            if len(res) >= n:
                break
        return sorted(res, key=lambda x: x["date"])

    def chercher(self, question: str, n: int = 5, exclure_sources: list[str] | None = None,
                 auj=None) -> list[dict]:
        from periode import periode  # import local : periode importe normaliser d'ici
        mots = mots_cles(question)
        per = periode(question, auj)
        if per:
            # mots de la période inutiles pour filtrer (octobre, semaine, dernier…)
            mots = [m for m in mots if m not in ("janvi", "fevri", "mars", "avril", "mai", "juin",
                    "juill", "aout", "septe", "octob", "novem", "decem", "semai", "derni",
                    "passe", "week", "lundi", "mardi", "mercr", "jeudi", "vendr", "samed",
                    "diman", "racon", "comme", "passe", "mois")]
            res = self.chercher_periode(per[0].isoformat(), per[1].isoformat(), mots,
                                        max(1, min(int(n), 20)), exclure_sources or [])
            if res:
                return res
        if not mots:
            return []
        requete = " OR ".join(f'"{m}"*' for m in mots)
        exclure = exclure_sources or []
        filtre = ""
        if exclure:
            filtre = " AND l.source NOT IN (%s)" % ",".join("?" * len(exclure))
        lignes = self.db.execute(
            "SELECT l.source, l.resume, l.detail, l.first_seen, l.date_evt, bm25(lignes_fts) AS score"
            " FROM lignes_fts JOIN lignes l ON l.id = lignes_fts.rowid"
            " WHERE lignes_fts MATCH ?" + filtre +
            " ORDER BY score, COALESCE(l.date_evt, l.first_seen) DESC LIMIT ?",
            (requete, *exclure, max(1, min(int(n), 20)) * 3),
        ).fetchall()
        # la même phrase peut venir d'une liste ET de l'archive : une seule fois
        vus: set[str] = set()
        res: list[dict] = []
        for r in lignes:
            cle = normaliser(r["resume"])[:120]
            if cle in vus:
                continue
            vus.add(cle)
            res.append({
                "source": r["source"],
                "date": r["date_evt"] or "",
                "texte": r["resume"],
                "detail": r["detail"],
            })
            if len(res) >= max(1, min(int(n), 20)):
                break
        return res

    def noter_ia(self, moteur: str, profondeur: str, ok: bool, duree: float | None,
                 raison: str | None, quand: float | None = None) -> None:
        import time
        self.db.execute("INSERT INTO ia_stats VALUES (?, ?, ?, ?, ?, ?)",
                        (quand or time.time(), moteur, profondeur, int(ok), duree, raison))
        self.db.commit()

    def stats_ia(self, depuis_s: float = 86400, maintenant: float | None = None) -> dict:
        import time
        t0 = (maintenant or time.time()) - depuis_s
        res = {}
        for r in self.db.execute(
            "SELECT moteur, COUNT(*), SUM(ok), AVG(CASE WHEN ok = 1 THEN duree END)"
            " FROM ia_stats WHERE quand >= ? GROUP BY moteur", (t0,)):
            res[r[0]] = {"appels": r[1], "ok": r[2] or 0,
                         "moy_s": round(r[3], 1) if r[3] is not None else None}
        return res

    def stats(self) -> dict:
        total = self.db.execute("SELECT COUNT(*) FROM lignes").fetchone()[0]
        par_source = {
            r[0]: r[1]
            for r in self.db.execute("SELECT source, COUNT(*) FROM lignes GROUP BY source")
        }
        plus_ancienne = self.db.execute(
            "SELECT MIN(COALESCE(date_evt, first_seen)) FROM lignes").fetchone()[0]
        return {"lignes": total, "par_source": par_source, "depuis": plus_ancienne}


LIGNE_ARCHIVE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\] \[([^\]]+)\] (.+)$")


def lire_archive(texte: str) -> list[dict]:
    """Lignes « [AAAA-MM-JJ] [source] texte » de l'archive /media/adria_memoire."""
    items = []
    for ligne in texte.splitlines():
        m = LIGNE_ARCHIVE.match(ligne.strip())
        if m:
            items.append({"date": m.group(1), "src": m.group(2), "summary": m.group(3)})
    return items


ETATS = {
    "on": "allumé", "off": "éteint", "home": "à la maison", "not_home": "absent",
    "playing": "en lecture", "paused": "en pause", "idle": "inactif", "standby": "en veille",
}


def lignes_historique(historique: list[list[dict]], noms: dict[str, str], fuseau=None) -> list[dict]:
    """Transforme la réponse /api/history/period en lignes datées (heure locale)."""
    items = []
    for serie in historique:
        precedent = None
        eid = serie[0].get("entity_id") if serie else None
        for etat in serie:
            eid = etat.get("entity_id") or eid
            s = etat.get("state")
            if s in (None, "unknown", "unavailable") or s == precedent:
                continue
            precedent = s
            quand = datetime.fromisoformat(etat["last_changed"]).astimezone(fuseau)
            nom = noms.get(eid, eid)
            items.append({
                "date": quand.strftime("%Y-%m-%d"),
                "summary": f"{quand.strftime('%d/%m %H:%M')} {nom} : {ETATS.get(s, s)}",
                "description": eid,
            })
    return items
