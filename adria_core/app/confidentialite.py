"""Politique de confidentialité de la mémoire (v0.21.2, fiches R1 et R6 du dossier de remédiation).

Un seul endroit décide de ce qu'un profil peut lire et de ce que la mémoire peut écrire. Le Core
applique ces règles LUI-MÊME : le client (HA, agent MCP) ne choisit jamais seul ses exclusions.

Profils :
- « adulte » : toute la mémoire (comportement d'avant pour Mael).
- « enfant » : RESTREINT, liste blanche SOURCES_ENFANT (celle déjà validée par Mael pour le cerveau
  général : souvenirs, épisodes, journal intérieur, menus) + procédures et compétences.
- « inconnu » (voix non reconnue, ou HA sans profil explicite) : liste blanche SOURCES_INCONNU,
  sans rien de la vie du foyer.
- Refus par défaut : une source nouvelle est fermée aux profils restreints tant qu'elle n'est pas
  ajoutée explicitement (ici ou par les options `memoire_sources_enfant` / `memoire_sources_inconnu`).
- tout autre valeur (vide, faute de frappe…) : REFUS complet.
"""

import re
import unicodedata

ADULTE, ENFANT, INCONNU = "adulte", "enfant", "inconnu"
PROFILS = (ADULTE, ENFANT, INCONNU)
RESTREINTS = (ENFANT, INCONNU)

# Listes blanches des profils restreints.
SOURCES_INCONNU = ("procedures", "competences", "menus")
# Reprise de general.SOURCES_ENFANT (validée par Mael) + procédures/compétences.
# + archives des épisodes et du journal intérieur : règle validée le 17/11 dans script.avatar_demander.
SOURCES_ENFANT = ("adria_souvenirs", "archive/souvenir", "adria_jarvis_episodes", "archive/episode",
                  "adria_journal_interieur", "archive/journal_interieur", "souvenirs_famille", "menus",
                  "procedures", "competences")

# Sources que l'ancien script HA (restreint=true) excluait : leur présence dans une demande signale
# un profil restreint, même si HA n'envoie pas encore le champ `profil`.
SIGNAL_RESTREINT = ("adria_memoire_perso", "archive/memoire_perso")


class AccesRefuse(PermissionError):
    pass


def normaliser_profil(valeur) -> str | None:
    v = str(valeur or "").strip().lower()
    return v if v in PROFILS else None


def profil_evenement(data: dict) -> str:
    """Profil d'une recherche reçue par l'événement adria_core_recherche.

    - `profil` explicite et valide : utilisé ; invalide : traité comme « inconnu » (jamais adulte) ;
    - `restreint: true` ou une exclusion de la mémoire personnelle (script HA actuel, qui ne distingue
      pas enfant et voix inconnue) : « inconnu », le plus strict ;
    - sinon : « adulte » (seul HA peut émettre cet événement sur son bus interne).
    """
    if "profil" in data and data.get("profil") not in (None, ""):
        return normaliser_profil(data.get("profil")) or INCONNU
    if str(data.get("restreint")).lower() == "true":
        return INCONNU
    exclues = data.get("exclure_sources") or []
    if isinstance(exclues, str):
        exclues = [exclues]
    if any(s in SIGNAL_RESTREINT for s in exclues):
        return INCONNU
    return ADULTE


class Politique:
    """`memoire` fournit `db` (sqlite3)."""

    def __init__(self, memoire, sources_enfant: list[str] | None = None,
                 sources_inconnu: list[str] | None = None):
        self.memoire = memoire
        self.autorisees = {ENFANT: tuple(sources_enfant) if sources_enfant else SOURCES_ENFANT,
                           INCONNU: tuple(sources_inconnu) if sources_inconnu else SOURCES_INCONNU}

    def verifier(self, profil) -> str:
        p = normaliser_profil(profil)
        if not p:
            raise AccesRefuse("profil absent ou inconnu : accès refusé")
        return p

    def peut_lire(self, profil: str, source: str) -> bool:
        p = normaliser_profil(profil)
        if p == ADULTE:
            return True
        if p in RESTREINTS:
            return source in self.autorisees[p]
        return False

    def exclusions(self, profil: str, demandees: list[str] | None = None) -> list[str]:
        """Sources à exclure : celles du client (en plus) + toutes celles que le profil ne lit pas."""
        p = self.verifier(profil)
        exclues = {str(s) for s in (demandees or []) if s}
        if p != ADULTE:
            toutes = {r[0] for r in self.memoire.db.execute("SELECT DISTINCT source FROM lignes")}
            exclues |= {s for s in toutes if s not in self.autorisees[p]}
        return sorted(exclues)

    def filtrer(self, profil: str, lignes: list[dict]) -> list[dict]:
        """Deuxième barrière, après la requête : aucune ligne hors politique ne sort."""
        return [l for l in lignes if self.peut_lire(profil, str(l.get("source") or ""))]


# ------------------------------------------------------------------ R6 : interdits d'écriture
def _plat(texte: str) -> str:
    t = unicodedata.normalize("NFKD", texte or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return f" {t.strip()} "


# Sujets interdits par Mael le 13/10 (ne JAMAIS recréer, reformuler ni déduire). Chaque sujet a
# plusieurs formes (synonymes, reformulations) ; la comparaison se fait sans accents ni ponctuation.
# Le texte du prompt reste dans consolidation.ENTETE ; ce tableau est le contrôle côté Python.
SUJETS_INTERDITS: dict[str, list[str]] = {
    "purge quotidienne de la base": [
        r"purge\w* (quotidien\w*|journalier\w*|chaque (jour|nuit)|tous les (jours|soirs|matins)|de nuit)",
        r"(quotidien\w*|journalier\w*|chaque (jour|nuit)) .{0,30}purg\w*",
        r"(vid\w*|nettoy\w*|effac\w*) .{0,25}(base|bdd|base de donnees) .{0,25}(chaque|tous|quotidien\w*)",
    ],
    "convalescence / veille sur Aelia": [
        r"convalesc\w*", r"(veill\w*|surveill\w*) (sur |de )?aelia", r"aelia .{0,20}(malade|veill\w*)",
        r"mael .{0,20}(se remet|en repos force|retablissement)",
    ],
    "préférence hygiène / maintenance proactive": [
        r"(prefer\w*|aime|tient a) .{0,30}(hygiene|maintenance (proactive|preventive))",
        r"maintenance (proactive|preventive)",
    ],
    "Nuit Douce / Nuit Économique / charge 21h30": [
        r"nuit (douce|eco\w*)", r"charge\w* .{0,15}21 ?h ?30", r"21 ?h ?30 .{0,15}charge\w*",
    ],
    "rendez-vous médicaux": [
        r"(rendez vous|rdv|consultation|visite) .{0,20}(medic\w*|medecin|docteur|dentiste|kine\w*|hopital|specialiste)",
        r"(medecin|docteur|dentiste|kine\w*|ophtalmo\w*|pediatre) .{0,20}(rendez vous|rdv|consult\w*)",
    ],
    "la Ruche": [r"\bla ruche\b"],
    "backlog AUTO_JAM": [r"auto ?jam"],
    "faux positifs cour = véhicules uniquement": [
        r"faux positif\w* .{0,40}(vehicule|voiture)\w* (uniquement|seulement)",
        r"(uniquement|seulement) .{0,20}(vehicule|voiture)\w* .{0,30}faux positif",
    ],
    "climatisation Inster stop auto": [
        r"(clim\w*|climatisation) .{0,30}inster .{0,30}(stop|arret|coup)\w*",
        r"inster .{0,30}(clim\w*) .{0,30}(stop|arret|coup)\w*",
    ],
    "priorisation via ADE": [r"\bade\b", r"(prioris\w*|priorit\w*) .{0,20}\bade\b"],
    "patterns voix top semaine": [r"(pattern|motif|habitude)\w* (voix|vocal\w*) .{0,20}(top|semaine|hebdo\w*)",
                                  r"top .{0,10}(voix|vocal\w*) .{0,15}semaine"],
}

_MOTIFS = {s: [re.compile(rf"(?<![a-z0-9]){m}") for m in ms] for s, ms in SUJETS_INTERDITS.items()}


def sujet_interdit(texte: str) -> str | None:
    """Nom du sujet interdit touché par ce texte, ou None."""
    plat = _plat(texte)
    for sujet, motifs in _MOTIFS.items():
        if any(m.search(plat) for m in motifs):
            return sujet
    return None
