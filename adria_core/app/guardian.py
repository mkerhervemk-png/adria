"""GUARDIAN — autorité indépendante d'ADRIA (ASCENSION S3, v0.22.0).

Le Guardian décide SANS IA, à partir d'un registre fixé dans ce fichier. Ni BRAIN, ni un modèle, ni les
arguments d'une demande ne peuvent élargir une permission : ils ne peuvent qu'en retirer.

Règles :
- Refus par défaut : un outil absent du registre est refusé.
- Profil invalide ou absent : refus complet (confidentialite.normaliser_profil).
- Classes d'opérations :
    LECTURE      -> autorisée selon le profil ;
    PREPARATION  -> proposition sans effet réel (texte), autorisée aux adultes ;
    ACTION       -> jamais exécutée par BRAIN : renvoyée comme proposition, il faut le OUI de Mael ;
    SENSIBLE     -> toujours refusée (sécurité, alarme, serrures, interrupteurs adria_core_*,
                    désarmement, mandats) — exclusion permanente.
- Arrêt d'urgence : input_boolean.adria_core_arret_urgence -> tout est refusé sauf etat_core, SAUF si son
  état est exactement « off » (v0.22.1 : inconnu, indisponible, absent ou illisible = arrêt actif).
- Panne : toute exception pendant la décision -> refus (le Guardian « tombe fermé »).
- Journal obligatoire (v0.22.1) : une décision qui n'a pas pu être écrite dans le journal devient un refus.
- Entités sensibles (v0.22.1) : UN SEUL filtre, `entite_autorisee()`, appliqué à toute classe d'opération
  (lecture comprise) et à tout chemin (entity_id exact, recherche par nom, historique).
- Plafond : au plus MAX_OUTILS_MISSION appels d'outils par mission.
- Journal infalsifiable : chaque décision est ajoutée à la table guardian_journal, chaînée par hash
  (SHA-256 de la ligne précédente + contenu). Aucune ligne n'est jamais modifiée ni effacée ;
  `verifier_chaine()` détecte toute retouche.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime

import confidentialite as CF

ARRET_URGENCE = "input_boolean.adria_core_arret_urgence"
MAX_OUTILS_MISSION = 8

LECTURE, PREPARATION, ACTION, SENSIBLE = "lecture", "preparation", "action", "sensible"

# Registre des outils : classe + profils autorisés. C'est la SEULE source des permissions.
REGISTRE: dict[str, dict] = {
    # mémoire (le Core filtre encore les lignes par profil : deuxième barrière)
    "memoire_chercher": {"classe": LECTURE, "profils": CF.PROFILS},
    "memoire_periode": {"classe": LECTURE, "profils": CF.PROFILS},
    "procedures_chercher": {"classe": LECTURE, "profils": CF.PROFILS},
    # vie du foyer et état technique : adultes seulement
    "evolution_journal": {"classe": LECTURE, "profils": (CF.ADULTE,)},
    "etat_core": {"classe": LECTURE, "profils": (CF.ADULTE,)},
    "carnet_dernier": {"classe": LECTURE, "profils": (CF.ADULTE,)},
    "habitudes": {"classe": LECTURE, "profils": (CF.ADULTE,)},
    "agenda": {"classe": LECTURE, "profils": (CF.ADULTE,)},
    "vue_foyer": {"classe": LECTURE, "profils": (CF.ADULTE,)},
    "etat_entite": {"classe": LECTURE, "profils": (CF.ADULTE,)},
    "historique_entite": {"classe": LECTURE, "profils": (CF.ADULTE,)},
    # propositions (texte seulement)
    "proposer_action": {"classe": PREPARATION, "profils": (CF.ADULTE,)},
    # actions réelles : existent dans le registre pour être classées, jamais exécutées par BRAIN
    "service_ha": {"classe": ACTION, "profils": (CF.ADULTE,)},
}

# Ce qui touche à la sécurité ou aux garde-fous d'ADRIA : toujours refusé, même proposé.
DOMAINES_SENSIBLES = ("alarm_control_panel", "lock", "camera", "siren")
ENTITES_SENSIBLES = (
    re.compile(r"^input_boolean\.adria_core_"),        # interrupteurs du Core (règle de préservation)
    re.compile(r"^input_(boolean|number|select|text|datetime|button)\.adria_"),  # réglages et garde-fous d'ADRIA
    re.compile(r"mandat"),                              # mandats d'autonomie, quel que soit le domaine
    re.compile(r"\.adria_core_"),                       # tout ce qui pilote ou décrit le Core
    re.compile(r"^input_boolean\.(alarme|securite|surveillance)"),
    re.compile(r"^input_boolean\.adria_claude_actif$"),
    re.compile(r"^input_boolean\.adria_ollama_secours_actif$"),
)
MOTS_SENSIBLES = re.compile(
    r"\b(desarm\w*|alarme\w*|serrure\w*|deverrouill\w*|verrou\w*|mandat\w*|code d acces|mot de passe|"
    r"adria core \w+|arret d urgence|sirene\w*)\b")  # texte mis à plat : « _ » devient une espace

# Lecture d'entités : domaines jamais lus par BRAIN (personnes géolocalisées exceptées pour l'adulte).
DOMAINES_ILLISIBLES = ("alarm_control_panel", "lock")


def _plat(t: str) -> str:
    return CF._plat(t).strip()


@dataclass
class Decision:
    autorise: bool
    classe: str
    raison: str
    besoin_accord: bool = False
    details: dict = field(default_factory=dict)

    def dict(self) -> dict:
        return {"autorise": self.autorise, "classe": self.classe, "raison": self.raison,
                "besoin_accord": self.besoin_accord}


def entite_sensible(entity_id: str) -> bool:
    e = str(entity_id or "").strip().lower()
    if e.split(".", 1)[0] in DOMAINES_SENSIBLES:
        return True
    return any(m.search(e) for m in ENTITES_SENSIBLES)


def entite_autorisee(entity_id: str) -> bool:
    """Filtre CENTRAL (v0.22.1) : une entité que BRAIN peut lire. Refusé : entités sensibles, domaines
    illisibles, et tout identifiant mal formé."""
    e = str(entity_id or "").strip().lower()
    if not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", e):
        return False
    return not entite_sensible(e) and e.split(".", 1)[0] not in DOMAINES_ILLISIBLES


def texte_sensible(texte: str) -> bool:
    return bool(MOTS_SENSIBLES.search(_plat(texte)))


class Guardian:
    """`memoire` fournit `db` (sqlite3) ; `etat` est une coroutine entity_id -> état (ou None)."""

    def __init__(self, memoire, etat=None):
        self.memoire = memoire
        self.etat = etat
        self.compteurs: dict[str, int] = {}
        self.refus = 0
        self.memoire.db.execute(
            "CREATE TABLE IF NOT EXISTS guardian_journal ("
            " id INTEGER PRIMARY KEY, quand TEXT NOT NULL, mission TEXT, profil TEXT, outil TEXT,"
            " classe TEXT, autorise INTEGER NOT NULL, raison TEXT, prec TEXT NOT NULL, hash TEXT NOT NULL)")
        self.memoire.db.commit()

    # ------------------------------------------------------------------ décision
    async def arret_urgence(self) -> bool:
        """Vrai (= arrêt ACTIF) sauf si l'état lu est exactement la chaîne « off ».
        Fonction de lecture absente, None, « on », « unknown », « unavailable », exception ou réponse d'un
        autre type : arrêt actif (fermé par défaut, v0.22.1)."""
        if not callable(self.etat):
            return True
        try:
            valeur = await self.etat(ARRET_URGENCE)
        except Exception:
            return True
        return not (isinstance(valeur, str) and valeur == "off")

    async def decider(self, outil: str, profil, args: dict | None = None, mission: str = "") -> Decision:
        try:
            d = await self._decider(str(outil or ""), profil, args or {}, mission)
        except Exception as e:  # le Guardian tombe toujours FERMÉ
            d = Decision(False, "?", f"contrôle impossible ({type(e).__name__}) : refus par sécurité")
        if not self.noter(mission, profil, outil, d) and d.autorise:
            d = Decision(False, d.classe, "journal du Guardian indisponible : refus par sécurité")
            self.noter(mission, profil, outil, d)  # tentative de trace du refus (sans effet si la base est en panne)
        if not d.autorise:
            self.refus += 1
        return d

    async def _decider(self, outil: str, profil, args: dict, mission: str) -> Decision:
        p = CF.normaliser_profil(profil)
        if not p:
            return Decision(False, "?", "profil absent ou inconnu")
        regle = REGISTRE.get(outil)
        if not regle:
            return Decision(False, "?", f"outil « {outil} » absent du registre (refus par défaut)")
        classe = regle["classe"]
        # Seule exception VOULUE : etat_core (lecture de la version et de la santé du Core, sans donnée du
        # foyer) reste permis pour diagnostiquer pourquoi tout est bloqué. Toute autre décision est refusée.
        if outil != "etat_core" and await self.arret_urgence():
            return Decision(False, classe, "arrêt d'urgence actif")
        if p not in regle["profils"]:
            return Decision(False, classe, f"outil « {outil} » réservé au profil adulte")
        n = self.compteurs.get(mission, 0) + 1
        if mission:
            self.compteurs[mission] = n
            if len(self.compteurs) > 500:  # pas de fuite mémoire : on garde les dernières missions
                for k in list(self.compteurs)[:250]:
                    self.compteurs.pop(k, None)
        if n > MAX_OUTILS_MISSION:
            return Decision(False, classe, f"plus de {MAX_OUTILS_MISSION} outils dans la mission")
        # contenu des arguments : la sécurité n'est jamais touchée, même en lecture
        cibles = [str(v) for k, v in args.items() if k in ("entity_id", "entite", "cible")]
        # filtre central, toutes classes : un entity_id (exact) sensible n'est jamais touché, même en lecture
        if any(re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", c.strip().lower()) and not entite_autorisee(c)
               for c in cibles):
            return Decision(False, SENSIBLE, "entité sensible (sécurité ou garde-fou d'ADRIA) : exclusion permanente")
        if classe in (PREPARATION, ACTION):
            texte = json.dumps(args, ensure_ascii=False)
            if texte_sensible(texte) or any(entite_sensible(c) for c in cibles):
                return Decision(False, SENSIBLE, "action de sécurité ou sur les garde-fous : exclusion permanente")
        if classe == ACTION:
            return Decision(False, ACTION, "action réelle : jamais exécutée par BRAIN, OUI de Mael requis",
                            besoin_accord=True)
        if classe == PREPARATION:
            return Decision(True, PREPARATION, "proposition sans effet", besoin_accord=True)
        return Decision(True, LECTURE, "lecture autorisée")

    def fin_mission(self, mission: str) -> None:
        self.compteurs.pop(mission, None)

    # ------------------------------------------------------------------ journal chaîné
    @staticmethod
    def _empreinte(prec: str, contenu: dict) -> str:
        return hashlib.sha256((prec + json.dumps(contenu, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()

    def noter(self, mission, profil, outil, d: Decision, quand: datetime | None = None) -> bool:
        """Ajoute la décision au journal chaîné. Renvoie False si l'écriture a échoué."""
        try:
            r = self.memoire.db.execute("SELECT hash FROM guardian_journal ORDER BY id DESC LIMIT 1").fetchone()
            prec = r[0] if r else "0" * 64
            contenu = {"quand": (quand or datetime.now()).isoformat(timespec="seconds"), "mission": str(mission or ""),
                       "profil": str(profil or ""), "outil": str(outil or ""), "classe": d.classe,
                       "autorise": int(d.autorise), "raison": d.raison[:200]}
            self.memoire.db.execute(
                "INSERT INTO guardian_journal (quand, mission, profil, outil, classe, autorise, raison, prec, hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (contenu["quand"], contenu["mission"], contenu["profil"], contenu["outil"], contenu["classe"],
                 contenu["autorise"], contenu["raison"], prec, self._empreinte(prec, contenu)))
            self.memoire.db.commit()
            return True
        except Exception:
            return False  # l'appelant transforme une autorisation non tracée en refus

    def verifier_chaine(self) -> dict:
        """{'ok': bool, 'lignes': n, 'rupture': id|None} — une ligne retouchée casse la chaîne."""
        prec = "0" * 64
        n = 0
        for r in self.memoire.db.execute(
                "SELECT id, quand, mission, profil, outil, classe, autorise, raison, prec, hash"
                " FROM guardian_journal ORDER BY id"):
            contenu = {"quand": r[1], "mission": r[2], "profil": r[3], "outil": r[4], "classe": r[5],
                       "autorise": r[6], "raison": r[7]}
            if r[8] != prec or r[9] != self._empreinte(prec, contenu):
                return {"ok": False, "lignes": n, "rupture": r[0]}
            prec = r[9]
            n += 1
        return {"ok": True, "lignes": n, "rupture": None}

    def stats(self) -> dict:
        r = self.memoire.db.execute(
            "SELECT COUNT(*), SUM(CASE WHEN autorise = 0 THEN 1 ELSE 0 END) FROM guardian_journal").fetchone()
        return {"decisions": r[0] or 0, "refus": r[1] or 0}

    def ancre(self) -> dict:
        """Tête de la chaîne {lignes, id, hash}, publiée dans HA (historique du recorder) : une chaîne
        entièrement recalculée dans la base du Core ne correspond plus aux ancres déjà publiées."""
        r = self.memoire.db.execute("SELECT id, hash FROM guardian_journal ORDER BY id DESC LIMIT 1").fetchone()
        n = self.memoire.db.execute("SELECT COUNT(*) FROM guardian_journal").fetchone()[0]
        return {"lignes": n, "id": r[0] if r else None, "hash": r[1] if r else None}

    def verifier_ancre(self, ancre: dict) -> bool:
        """Vrai si la ligne `id` porte toujours le hash publié (et si la chaîne est intègre)."""
        if not ancre or not ancre.get("id"):
            return True
        r = self.memoire.db.execute("SELECT hash FROM guardian_journal WHERE id = ?", (int(ancre["id"]),)).fetchone()
        return bool(r) and r[0] == ancre.get("hash") and self.verifier_chaine()["ok"]
