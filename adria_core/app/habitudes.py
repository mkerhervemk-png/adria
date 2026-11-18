"""Habitudes repérées (lot H1) : ADRIA remarque ce qui revient et le PROPOSE, elle n'applique jamais seule.

- Calcul sans IA (0 token) sur l'historique gardé par le Core (35 derniers jours).
- Situation = quart de Mael (matin, après-midi, nuit, repos), jour de la semaine, ou les deux.
- Une habitude est retenue si elle arrive au moins 3 fois sur les 5 dernières situations semblables
  (et au moins 4 situations observées).
- Le dimanche à 17h30 : 3 propositions au maximum, boutons OUI / NON / Plus tard.
  OUI = un rappel simple (notification, seulement si le problème est vraiment là à ce moment),
  NON = jamais reproposé, Plus tard = pas avant 14 jours. Chaque rappel a un bouton « Arrêter ».
- Interrupteur : input_boolean.adria_core_habitudes (OFF = rien n'est proposé ni rappelé ; le calcul
  reste visible dans sensor.adria_core_habitudes).

Règles observées (liste fermée, chacune avec son rappel) :
- inster : l'Inster est à la maison mais pas branchée à 21h00 -> rappel à 20h45.
- lumiere : des lumières restent allumées 20 min après le départ de Mael alors que personne d'autre
  n'est à la maison -> rappel à son départ, s'il en reste d'allumées et que la maison est vide.
- tv_nuit : la TV du salon est encore allumée 10 min après le passage en mode nuit -> rappel au
  passage en mode nuit si elle est allumée.
"""

import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from store import ETATS

LOG = logging.getLogger("adria_core.habitudes")

INTERRUPTEUR = "input_boolean.adria_core_habitudes"
FUSEAU = ZoneInfo("Europe/Paris")
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
QUARTS = {"matin": "du matin", "apres_midi": "d'après-midi", "nuit": "de nuit", "repos": "de repos"}
FENETRE_JOURS = 35
MIN_SITUATIONS, SUR, SEUIL = 4, 5, 3
MAX_PROPOSITIONS = 3
DELAI_PLUS_TARD = 14
QUART = "input_select.quart_mael"
MAEL = "person.mael_kerherve"
NUIT = "input_boolean.mode_nuit"
TV = "media_player.lg_webos_tv_uj630v"
INSTER_LIEU = "device_tracker.inster_location"
INSTER_PRISE = "binary_sensor.inster_ev_battery_plug"
LUMIERES_EXCLUES = re.compile(r"exterieur|abri|jardin|guirlande|noel|chambre_parent|parentale", re.I)
INVERSE = {v: k for k, v in ETATS.items()}
LIGNE = re.compile(r"^(\d{2})/(\d{2}) (\d{2}):(\d{2}) (.+) : (.*)$")

REGLES = {
    "inster": {"titre": "🔌 Inster",
               "constat": "l'Inster était à la maison mais pas branchée à 21 h",
               "rappel": "je te le rappelle ces jours-là à 20 h 45, seulement si elle n'est pas branchée"},
    "lumiere": {"titre": "💡 Lumières",
                "constat": "des lumières restaient allumées après ton départ, maison vide",
                "rappel": "je te préviens au moment où tu pars, seulement s'il en reste d'allumées et que personne n'est à la maison"},
    "tv_nuit": {"titre": "📺 TV du salon",
                "constat": "la TV du salon était encore allumée après le passage en mode nuit",
                "rappel": "je te préviens au passage en mode nuit, seulement si elle est allumée"},
}


def maintenant_local() -> datetime:
    return datetime.now(FUSEAU).replace(tzinfo=None)


# ----------------------------------------------------------------------------- lecture de l'historique
def lire_lignes(lignes: list[tuple]) -> dict[str, list[tuple[datetime, str]]]:
    """(resume, detail=entity_id, date_evt) -> {entity_id: [(heure locale, état brut)]} trié."""
    res: dict[str, list] = {}
    for resume, eid, date_evt in lignes:
        m = LIGNE.match(resume or "")
        if not m or not eid or not date_evt:
            continue
        try:
            quand = datetime(int(date_evt[:4]), int(m.group(2)), int(m.group(1)),
                             int(m.group(3)), int(m.group(4)))
        except ValueError:
            continue
        etat = m.group(6).strip()
        res.setdefault(eid, []).append((quand, INVERSE.get(etat, etat)))
    for v in res.values():
        v.sort()
    return res


def etat_a(serie: list[tuple[datetime, str]] | None, quand: datetime) -> str | None:
    dernier = None
    for t, e in serie or []:
        if t > quand:
            break
        dernier = e
    return dernier


def transitions(serie, debut: datetime, fin: datetime):
    prec = None
    for t, e in serie or []:
        if t >= debut and t < fin and prec is not None and e != prec:
            yield t, prec, e
        prec = e


# ----------------------------------------------------------------------------- les règles, jour par jour
def observer(h: dict, jour: date) -> dict[str, bool | None]:
    """Pour un jour : True = oubli, False = tout allait bien, None = règle sans objet ce jour-là."""
    d0 = datetime.combine(jour, datetime.min.time())
    res: dict[str, bool | None] = {}
    # inster
    t = d0 + timedelta(hours=21)
    lieu, prise = etat_a(h.get(INSTER_LIEU), t), etat_a(h.get(INSTER_PRISE), t)
    res["inster"] = None if lieu != "home" or prise not in ("on", "off") else prise == "off"
    # lumiere : 1er départ de Mael dans la journée
    depart = next((t for t, a, b in transitions(h.get(MAEL), d0, d0 + timedelta(days=1))
                   if a == "home" and b not in ("home", "inconnu")), None)
    autres = [e for e in h if e.startswith("person.") and e != MAEL]
    if depart is None or any(etat_a(h[e], depart + timedelta(minutes=20)) == "home" for e in autres):
        res["lumiere"] = None  # quelqu'un est resté à la maison : ce n'est pas un oubli
    else:
        lum = [e for e in h if e.startswith("light.") and not LUMIERES_EXCLUES.search(e)]
        res["lumiere"] = any(etat_a(h[e], depart + timedelta(minutes=20)) == "on" for e in lum)
    # tv_nuit
    nuit = next((t for t, a, b in transitions(h.get(NUIT), d0, d0 + timedelta(days=1))
                 if a == "off" and b == "on"), None)
    res["tv_nuit"] = None if nuit is None or h.get(TV) is None else \
        etat_a(h.get(TV), nuit + timedelta(minutes=10)) not in (None, "off", "standby")
    return res


def situations(quart: str | None, jour: date) -> list[str]:
    """Clés de la plus précise à la plus large."""
    j = JOURS[jour.weekday()]
    cles = [f"jour:{j}"]
    if quart in QUARTS:
        cles = [f"jour+quart:{j}|{quart}", f"quart:{quart}"] + cles
    return cles


def libelle_situation(cle: str) -> str:
    typ, _, val = cle.partition(":")
    if typ == "quart":
        return "les jours de repos" if val == "repos" else f"les jours de quart {QUARTS[val]}"
    if typ == "jour":
        return f"le {val}"
    j, q = val.split("|")
    return f"le {j} quand tu es {QUARTS[q]}"


def candidats(h: dict, aujourdhui: date) -> list[dict]:
    """Habitudes qui passent le seuil, la situation la plus précise d'abord, une par règle."""
    par_cle: dict[tuple[str, str], list[tuple[date, bool]]] = {}
    for k in range(FENETRE_JOURS, 0, -1):
        jour = aujourdhui - timedelta(days=k)
        quart = etat_a(h.get(QUART), datetime.combine(jour, datetime.min.time()) + timedelta(hours=12))
        for regle, oubli in observer(h, jour).items():
            if oubli is None:
                continue
            for cle in situations(quart, jour):
                par_cle.setdefault((regle, cle), []).append((jour, oubli))
    res: dict[str, dict] = {}
    for (regle, cle), obs in par_cle.items():
        if len(obs) < MIN_SITUATIONS:
            continue
        derniers = obs[-SUR:]
        n = sum(1 for _, o in derniers if o)
        if n < SEUIL:
            continue
        precision = 0 if cle.startswith("jour+quart") else (1 if cle.startswith("quart") else 2)
        c = {"regle": regle, "cle": cle, "oublis": n, "sur": len(derniers), "precision": precision,
             "dates": [j.strftime("%d/%m") for j, o in derniers if o]}
        if regle not in res or (precision, -n / len(derniers)) < (res[regle]["precision"],
                                                                   -res[regle]["oublis"] / res[regle]["sur"]):
            res[regle] = c
    return sorted(res.values(), key=lambda c: (-c["oublis"] / c["sur"], c["precision"]))


def message(c: dict) -> str:
    r = REGLES[c["regle"]]
    return (f"{libelle_situation(c['cle']).capitalize()}, {r['constat']} {c['oublis']} fois sur {c['sur']} "
            f"({', '.join(c['dates'])}). Proposition : {r['rappel']}. D'accord ?")


def situation_actuelle(cle: str, quart: str | None, quand: datetime) -> bool:
    return cle in situations(quart, quand.date())


# ----------------------------------------------------------------------------- l'agent
class AgentHabitudes:
    """`core` = le Core : etat(), appel(), memoire ; `notifier` = service de notification."""

    def __init__(self, core, notifier: str) -> None:
        self.core = core
        self.notifier = notifier
        self.derniers: list[dict] = []
        self.precedent: dict[str, str | None] = {}
        self.fait_le: dict[str, str] = {}

    # ---- mémoire de l'agent (table meta : réglages, jamais les lignes de la mémoire)
    def _lire(self, cle: str, defaut):
        try:
            return json.loads(self.core.memoire.meta(cle) or "null") or defaut
        except ValueError:
            return defaut

    def _ecrire(self, cle: str, valeur) -> None:
        self.core.memoire.meta(cle, json.dumps(valeur, ensure_ascii=False))

    @property
    def attentes(self) -> dict:
        """Propositions envoyées sans réponse (gardées : survivent à un redémarrage du Core)."""
        return self._lire("hab_attentes", {})

    @property
    def rappels(self) -> list[dict]:
        return self._lire("hab_rappels", [])

    def historique(self, aujourdhui: date) -> dict:
        debut = (aujourdhui - timedelta(days=FENETRE_JOURS + 3)).isoformat()
        lignes = self.core.memoire.db.execute(
            "SELECT resume, detail, date_evt FROM lignes WHERE source = 'historique' AND date_evt >= ?"
            " AND (detail LIKE 'light.%' OR detail LIKE 'person.%' OR detail IN (?, ?, ?, ?, ?, ?))",
            (debut, QUART, MAEL, NUIT, TV, INSTER_LIEU, INSTER_PRISE)).fetchall()
        return lire_lignes([tuple(r) for r in lignes])

    def calculer(self, aujourdhui: date | None = None) -> list[dict]:
        aujourdhui = aujourdhui or maintenant_local().date()
        cands = candidats(self.historique(aujourdhui), aujourdhui)
        refus = set(self._lire("hab_refus", []))
        tard = self._lire("hab_tard", {})
        actifs = {f"{r['regle']}|{r['cle']}" for r in self.rappels if r.get("actif")}
        for c in cands:
            k = f"{c['regle']}|{c['cle']}"
            c["statut"] = ("refusé" if k in refus else "rappel actif" if k in actifs else
                           "plus tard" if tard.get(k, "") > aujourdhui.isoformat() else "à proposer")
        self.derniers = cands
        return cands

    async def publier(self) -> None:
        try:
            await self.core.appel("POST", "/states/sensor.adria_core_habitudes", {
                "state": str(sum(1 for c in self.derniers if c["statut"] == "à proposer")),
                "attributes": {"friendly_name": "ADRIA Core — habitudes repérées", "icon": "mdi:repeat",
                               "candidats": [{**c, "texte": message(c)} for c in self.derniers],
                               "rappels": [r for r in self.rappels if r.get("actif")]}})
        except Exception as e:
            LOG.warning("Etat habitudes non publié : %s", e)

    async def notifier_tel(self, titre: str, texte: str, actions=None, tag: str = "adria_habitude") -> None:
        domaine, service = self.notifier.split(".", 1)
        data = {"tag": tag, "channel": "ADRIA habitudes"}
        if actions:
            data["actions"] = actions
        await self.core.appel("POST", f"/services/{domaine}/{service}",
                              {"title": titre, "message": texte, "data": data})

    # ---- le dimanche : propositions
    async def proposer(self, essai: bool = False, forcer: bool = False) -> dict:
        auj = maintenant_local().date()
        cands = self.calculer(auj)
        res = {"quand": maintenant_local().isoformat(timespec="seconds"), "candidats": len(cands),
               "proposees": 0, "essai": essai}
        actif = await self.core.etat(INTERRUPTEUR) == "on"
        if (auj.weekday() != 6 and not forcer) or essai or not actif:
            res["raison"] = "essai" if essai else ("interrupteur OFF" if not actif else "pas dimanche")
            await self.publier()
            return res
        for c in [c for c in cands if c["statut"] == "à proposer"][:MAX_PROPOSITIONS]:
            ident = uuid.uuid4().hex[:8]
            att = self.attentes
            att[ident] = c
            self._ecrire("hab_attentes", att)
            await self.notifier_tel(f"{REGLES[c['regle']]['titre']} — habitude repérée", message(c), [
                {"action": f"ADRIA_HAB_{ident}_OUI", "title": "OUI"},
                {"action": f"ADRIA_HAB_{ident}_NON", "title": "NON"},
                {"action": f"ADRIA_HAB_{ident}_TARD", "title": "Plus tard"}], tag=f"adria_hab_{ident}")
            res["proposees"] += 1
        LOG.info("Habitudes : %s", res)
        await self.publier()
        return res

    def recevoir_action(self, data: dict) -> str | None:
        action = str(data.get("action") or "")
        if not action.startswith("ADRIA_HAB_"):
            return None
        _, _, ident, choix = action.split("_", 3)
        if choix == "STOP":
            rappels = self.rappels
            for r in rappels:
                if r.get("id") == ident:
                    r["actif"] = False
                    r["arrete_le"] = maintenant_local().date().isoformat()
            self._ecrire("hab_rappels", rappels)
            return "STOP"
        att = self.attentes
        c = att.pop(ident, None)
        if not c:
            return None
        self._ecrire("hab_attentes", att)
        k = f"{c['regle']}|{c['cle']}"
        if choix == "OUI":
            rappels = self.rappels
            rappels.append({"id": ident, "regle": c["regle"], "cle": c["cle"], "actif": True,
                            "depuis": maintenant_local().date().isoformat()})
            self._ecrire("hab_rappels", rappels)
        elif choix == "NON":
            self._ecrire("hab_refus", sorted(set(self._lire("hab_refus", [])) | {k}))
        elif choix == "TARD":
            tard = self._lire("hab_tard", {})
            tard[k] = (maintenant_local().date() + timedelta(days=DELAI_PLUS_TARD)).isoformat()
            self._ecrire("hab_tard", tard)
        for cc in self.derniers:
            if f"{cc['regle']}|{cc['cle']}" == k:
                cc["statut"] = {"OUI": "rappel actif", "NON": "refusé", "TARD": "plus tard"}.get(choix, cc["statut"])
        return choix

    # ---- chaque minute : les rappels acceptés
    async def minute(self, quand: datetime | None = None) -> list[str]:
        rappels = [r for r in self.rappels if r.get("actif")]
        envoyes: list[str] = []
        if not rappels:
            return envoyes
        quand = quand or maintenant_local()
        if await self.core.etat(INTERRUPTEUR) != "on":
            return envoyes
        e = {x: await self.core.etat(x) for x in (QUART, MAEL, NUIT, TV, INSTER_LIEU, INSTER_PRISE)}
        avant, self.precedent = self.precedent, e
        jour = quand.date().isoformat()
        for r in rappels:
            if not situation_actuelle(r["cle"], e[QUART], quand) or self.fait_le.get(r["id"]) == jour:
                continue
            texte = None
            if r["regle"] == "inster" and quand.strftime("%H:%M") == "20:45":
                if e[INSTER_LIEU] == "home" and e[INSTER_PRISE] == "off":
                    texte = "L'Inster est à la maison mais pas branchée."
            elif r["regle"] == "lumiere" and avant.get(MAEL) == "home" and e[MAEL] not in ("home", None):
                etats = await self.core.appel("GET", "/states")
                if any(s["entity_id"].startswith("person.") and s["entity_id"] != MAEL and s.get("state") == "home"
                       for s in etats):
                    continue  # quelqu'un est encore à la maison
                allumees = [s.get("attributes", {}).get("friendly_name") or s["entity_id"] for s in etats
                            if s["entity_id"].startswith("light.") and s.get("state") == "on"
                            and not LUMIERES_EXCLUES.search(s["entity_id"])]
                if allumees:
                    texte = "Tu pars : encore allumé(es) — " + ", ".join(allumees[:6]) + "."
            elif r["regle"] == "tv_nuit" and avant.get(NUIT) == "off" and e[NUIT] == "on":
                if e[TV] not in (None, "off", "standby", "unavailable"):
                    texte = "Mode nuit : la TV du salon est encore allumée."
            if texte:
                self.fait_le[r["id"]] = jour
                await self.notifier_tel(REGLES[r["regle"]]["titre"], texte,
                                        [{"action": f"ADRIA_HAB_{r['id']}_STOP", "title": "Arrêter ce rappel"}],
                                        tag=f"adria_hab_rappel_{r['regle']}")
                envoyes.append(r["regle"])
        return envoyes
