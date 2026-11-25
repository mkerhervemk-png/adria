"""Mémoire fiable (ASCENSION S4, v0.22.0) : provenance, confiance, « je sais / je suppose / j'ignore »,
contradictions.

Strictement ADDITIF : la table `lignes` n'est pas modifiée (règle du 17/11 : rien n'est jamais effacé).
La confiance est CALCULÉE sans IA à partir de la nature de la source et de l'âge de l'information ;
les contradictions repérées sont ajoutées à la table `contradictions` (jamais supprimées : on change
seulement leur statut quand Mael tranche).

Nature d'une source :
- observé : relevé par la maison ou un document (historique HA, agenda, papiers, courriers…) ;
- déclaré : dit ou validé par Mael (mémoire perso, savoir, faits consolidés, procédures…) ;
- raconté : récit écrit par ADRIA (journal, épisodes, souvenirs, carnet) ;
- déduit : produit par une IA (réponses du cerveau général, analyses) — jamais traité comme un fait sûr.
"""

import re
from datetime import date, datetime

from store import normaliser

OBSERVE, DECLARE, RACONTE, DEDUIT = "observé", "déclaré", "raconté", "déduit"

NATURE: dict[str, tuple[str, float]] = {
    "historique": (OBSERVE, 0.9), "agenda": (OBSERVE, 0.85), "papiers": (OBSERVE, 0.95),
    "suivis": (OBSERVE, 0.9), "budget": (OBSERVE, 0.85), "famille": (OBSERVE, 0.85),
    "telephones": (OBSERVE, 0.8), "piles": (OBSERVE, 0.8), "reseau": (OBSERVE, 0.8),
    "cameras": (OBSERVE, 0.8), "indisponibles": (OBSERVE, 0.8), "mises_a_jour": (OBSERVE, 0.8),
    "automatisations_ko": (OBSERVE, 0.8), "devoirs": (OBSERVE, 0.8),
    "adria_memoire_perso": (DECLARE, 0.85), "archive/memoire_perso": (DECLARE, 0.85),
    "adria_savoir": (DECLARE, 0.85), "adria_memoire": (DECLARE, 0.75), "archive/memoire": (DECLARE, 0.75),
    "procedures": (DECLARE, 0.85), "competences": (DECLARE, 0.8), "menus": (DECLARE, 0.7),
    "souvenirs_famille": (DECLARE, 0.8),
    "adria_journal": (RACONTE, 0.6), "archive/journal": (RACONTE, 0.6),
    "adria_jarvis_episodes": (RACONTE, 0.6), "archive/episode": (RACONTE, 0.6),
    "adria_souvenirs": (RACONTE, 0.65), "archive/souvenir": (RACONTE, 0.65),
    "adria_journal_interieur": (RACONTE, 0.5), "archive/journal_interieur": (RACONTE, 0.5),
    "adria_observations": (RACONTE, 0.6), "archive/observation": (RACONTE, 0.6),
    "archive/temoin": (RACONTE, 0.6),
    "general": (DEDUIT, 0.3), "veille": (DEDUIT, 0.55), "core_ia": (DEDUIT, 0.4),
    "sante_ia": (DEDUIT, 0.5), "evolution": (DEDUIT, 0.6),
}
DEFAUT = (RACONTE, 0.5)
SEUIL_SAIT = 0.7
# Les états qui changent vite (lumières, présence…) perdent vite leur valeur de « fait actuel ».
SOURCES_VOLATILES = ("historique", "veille", "telephones", "reseau", "indisponibles")
CONSOLIDE = re.compile(r"src=consolide")


def nature(source: str, detail: str = "") -> tuple[str, float]:
    if CONSOLIDE.search(detail or ""):
        return DECLARE, 0.8  # fait consolidé de nuit à partir de la mémoire validée
    return NATURE.get(source or "", DEFAUT)


def _jour(v) -> date | None:
    try:
        return datetime.fromisoformat(str(v or "")[:10]).date()
    except ValueError:
        return None


def confiance(source: str, date_evt: str = "", detail: str = "", auj: date | None = None) -> float:
    """0..1. Base = nature de la source ; les états volatils perdent 0,05 par jour (plancher 0,3)."""
    _, base = nature(source, detail)
    j = _jour(date_evt)
    if j and source in SOURCES_VOLATILES:
        age = max(0, ((auj or date.today()) - j).days)
        base = max(0.3, base - 0.05 * age)
    return round(base, 2)


def annoter(lignes: list[dict], auj: date | None = None) -> list[dict]:
    """Ajoute à chaque ligne : nature, confiance, age_jours (sans modifier la base)."""
    auj = auj or date.today()
    res = []
    for i, l in enumerate(lignes):
        src, det = str(l.get("source") or ""), str(l.get("detail") or "")
        nat, _ = nature(src, det)
        j = _jour(l.get("date"))
        res.append({**l, "nature": nat, "confiance": confiance(src, l.get("date") or "", det, auj),
                    "age_jours": (auj - j).days if j else None, "ref": l.get("ref") or f"M{i + 1}"})
    return res


def statut(lignes: list[dict]) -> str:
    """« sait » si une ligne fiable existe, « suppose » si seulement des lignes faibles, sinon « ignore »."""
    if not lignes:
        return "ignore"
    meilleure = max(float(l.get("confiance") or 0) for l in lignes)
    return "sait" if meilleure >= SEUIL_SAIT else "suppose"


# ------------------------------------------------------------------ contradictions
_MOTS_VIDES = {"les", "des", "une", "est", "que", "qui", "pour", "avec", "dans", "sur", "par", "pas", "mais",
               "plus", "tout", "elle", "ils", "nous", "vous", "aux", "ont", "ete", "son", "ses", "mon", "mes",
               "the", "and", "a", "au", "du", "de", "la", "le", "et", "en", "il", "on", "un"}
_NEGATION = re.compile(r"\b(ne|n|pas|plus|jamais|aucun\w*|annul\w*|supprim\w*|desactiv\w*)\b")
_VALEUR = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:h\d{0,2}|€|euros?|km|%|ans?)?")


def _contenu(t: str) -> set[str]:
    return {m for m in re.findall(r"[a-z]{3,}", normaliser(t)) if m not in _MOTS_VIDES}


def _valeurs(t: str) -> set[str]:
    sans_date = re.sub(r"\b\d{1,4}[-/]\d{1,2}([-/]\d{1,4})?\b", " ", normaliser(t))
    return {v.replace(" ", "").replace(",", ".") for v in _VALEUR.findall(sans_date)}


_HORODATAGE = re.compile(r"\b(\d{1,2}[:h]\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2})\b")


def _horodatages(t: str) -> set[str]:
    return {m.replace("h", ":") for m in _HORODATAGE.findall(normaliser(t))}


def contradictions(lignes: list[dict], seuil: float = 0.5) -> list[dict]:
    """Paires de lignes qui parlent de la même chose (≥ 50 % de mots communs, au moins 3) mais avec des
    valeurs différentes ou une négation d'un seul côté. Heuristique prudente : signale, ne tranche pas.
    v0.22.3 : deux lignes qui portent des heures ou des dates DIFFÉRENTES décrivent deux moments différents
    (journal du 01/11 et du 02/11, lumière éteinte à 14:18 puis à 23:50, départs à 08:44 et 10:26) : ce n'est
    pas une contradiction. Une réponse d'IA (nature « déduit ») n'est jamais comparée."""
    res = []
    for i, a in enumerate(lignes):
        for b in lignes[i + 1:]:
            if DEDUIT in (a.get("nature"), b.get("nature")):
                continue
            ta, tb = str(a.get("texte") or ""), str(b.get("texte") or "")
            ha, hb = _horodatages(ta), _horodatages(tb)
            if ha and hb and ha != hb:
                continue
            ca, cb = _contenu(ta), _contenu(tb)
            communs = ca & cb
            if len(communs) < 3 or len(communs) / max(1, min(len(ca), len(cb))) < seuil:
                continue
            va, vb = _valeurs(ta), _valeurs(tb)
            neg_a, neg_b = bool(_NEGATION.search(normaliser(ta))), bool(_NEGATION.search(normaliser(tb)))
            if (va and vb and va != vb) or neg_a != neg_b:
                # la plus récente, puis la plus fiable, est retenue comme « à privilégier »
                cle = lambda x: (str(x.get("date") or ""), float(x.get("confiance") or 0))  # noqa: E731
                retenue, autre = (a, b) if cle(a) >= cle(b) else (b, a)
                res.append({"retenue": retenue.get("ref"), "autre": autre.get("ref"),
                            "texte_retenu": str(retenue.get("texte"))[:200], "texte_autre": str(autre.get("texte"))[:200],
                            "motif": "valeurs différentes" if va != vb and va and vb else "négation"})
    return res


class Registre:
    """Table `contradictions` (ajout seul ; le statut passe de « ouverte » à « tranchée »)."""

    def __init__(self, memoire):
        self.memoire = memoire
        self.memoire.db.execute(
            "CREATE TABLE IF NOT EXISTS contradictions (id INTEGER PRIMARY KEY, quand TEXT NOT NULL,"
            " texte_a TEXT NOT NULL, texte_b TEXT NOT NULL, motif TEXT, statut TEXT NOT NULL DEFAULT 'ouverte',"
            " UNIQUE(texte_a, texte_b))")
        self.memoire.db.commit()

    def noter(self, liste: list[dict], quand: datetime | None = None) -> int:
        n = 0
        q = (quand or datetime.now()).isoformat(timespec="seconds")
        for c in liste:
            a, b = sorted((c["texte_retenu"], c["texte_autre"]))
            cur = self.memoire.db.execute(
                "INSERT OR IGNORE INTO contradictions (quand, texte_a, texte_b, motif) VALUES (?, ?, ?, ?)",
                (q, a, b, c.get("motif")))
            n += cur.rowcount
        self.memoire.db.commit()
        return n

    def ouvertes(self, n: int = 20) -> list[dict]:
        return [dict(r) for r in self.memoire.db.execute(
            "SELECT id, quand, texte_a, texte_b, motif FROM contradictions WHERE statut = 'ouverte'"
            " ORDER BY id DESC LIMIT ?", (n,))]

    def trancher(self, ident: int) -> bool:
        cur = self.memoire.db.execute("UPDATE contradictions SET statut = 'tranchée' WHERE id = ?", (int(ident),))
        self.memoire.db.commit()
        return bool(cur.rowcount)
