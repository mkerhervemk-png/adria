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
    "souviens", "rappelle", "rappelles", "hier", "jour", "fois",
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
                last_seen TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS lignes_fts USING fts5(
                resume, detail, tokenize = 'unicode61 remove_diacritics 2'
            );
            """
        )
        self.db.commit()

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
                "INSERT INTO lignes (source, uid, resume, detail, empreinte, first_seen, last_seen)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source, it.get("uid"), resume, detail, empreinte, quand, quand),
            )
            self.db.execute(
                "INSERT INTO lignes_fts (rowid, resume, detail) VALUES (?, ?, ?)",
                (cur.lastrowid, resume, detail),
            )
            nouvelles += 1
        self.db.commit()
        return nouvelles

    def chercher(self, question: str, n: int = 5, exclure_sources: list[str] | None = None) -> list[dict]:
        mots = mots_cles(question)
        if not mots:
            return []
        requete = " OR ".join(f'"{m}"*' for m in mots)
        exclure = exclure_sources or []
        filtre = ""
        if exclure:
            filtre = " AND l.source NOT IN (%s)" % ",".join("?" * len(exclure))
        lignes = self.db.execute(
            "SELECT l.source, l.resume, l.detail, l.first_seen, bm25(lignes_fts) AS score"
            " FROM lignes_fts JOIN lignes l ON l.id = lignes_fts.rowid"
            " WHERE lignes_fts MATCH ?" + filtre +
            " ORDER BY score, l.first_seen DESC LIMIT ?",
            (requete, *exclure, max(1, min(int(n), 20))),
        ).fetchall()
        return [
            {
                "source": r["source"],
                "date": r["first_seen"][:10],
                "texte": r["resume"],
                "detail": r["detail"],
            }
            for r in lignes
        ]

    def stats(self) -> dict:
        total = self.db.execute("SELECT COUNT(*) FROM lignes").fetchone()[0]
        par_source = {
            r[0]: r[1]
            for r in self.db.execute("SELECT source, COUNT(*) FROM lignes GROUP BY source")
        }
        plus_ancienne = self.db.execute("SELECT MIN(first_seen) FROM lignes").fetchone()[0]
        return {"lignes": total, "par_source": par_source, "depuis": plus_ancienne}
