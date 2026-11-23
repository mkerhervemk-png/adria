"""Agent Analyse, lot AN1 (23/11/2026, validé par Mael) : « HA détecte, le Core réfléchit ».

Reprend « ADRIA — Détection anomalies maison » (automation.ia_detection_anomalies_maison) qui posait
toutes les 6 h la question à l'IA de la voix (chaîne interactive, quota Gemini) pour 8 valeurs.
Ici : mêmes contrôles, et un peu plus, SANS IA (0 token), à 08h00, 12h00 et 18h00.

- Une seule notification par anomalie et par jour (clé stable), comme l'agent Maintenance.
- Interrupteur : input_boolean.adria_core_analyse (OFF = calcul visible dans le capteur,
  aucune notification). Événement adria_core_analyse {essai: true} = calcul sans notification.
- Capteur : sensor.adria_core_analyse (ok / anomalie). Ne pilote rien dans la maison.
"""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

LOG = logging.getLogger("adria_core.analyse")

INTERRUPTEUR = "input_boolean.adria_core_analyse"
CAPTEUR = "sensor.adria_core_analyse"
FUSEAU = ZoneInfo("Europe/Paris")
HEURES = ("08:00", "12:00", "18:00")

GUIRLANDE = ("light.chambre_aelia_guirlande_aelia_prise_1", "switch.guirlande_aelia_socket_1")
EXTERIEUR = ("light.abris_voiture", "light.exterieur", "light.garage")
TABLETTE = "sensor.sm_t720_battery_level"
SEUIL_TABLETTE = 15
VEILLEUSES = ("light.h618a",)  # lampes de bureau des enfants = veilleuses (règle Mael 19/10)


def _nom(e: dict) -> str:
    return (e.get("attributes") or {}).get("friendly_name") or e["entity_id"]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def detecter(etats: list[dict], maintenant: datetime) -> list[dict]:
    """Etats HA -> anomalies [{cle, texte}]. Sans IA."""
    par = {e["entity_id"]: e for e in etats}
    st = lambda i: (par.get(i) or {}).get("state")  # noqa: E731
    out: list[dict] = []
    personnes = [e for e in etats if e["entity_id"].startswith("person.")]
    vide = bool(personnes) and all(e["state"] != "home" for e in personnes)
    lumieres = [e for e in etats if e["entity_id"].startswith("light.") and e["state"] == "on"
                and "entity_id" not in (e.get("attributes") or {})]  # sans les groupes
    if vide:
        allumees = [_nom(e) for e in lumieres if e["entity_id"] not in VEILLEUSES]
        if allumees:
            out.append({"cle": "lumieres_maison_vide",
                        "texte": "Maison vide mais lumière(s) allumée(s) : " + ", ".join(sorted(allumees)[:4])})
    jour = st("sun.sun") == "above_horizon"
    if jour:
        ext = [_nom(par[i]) for i in EXTERIEUR if st(i) == "on"]
        if ext:
            out.append({"cle": "exterieur_jour", "texte": "Lumière extérieure allumée en plein jour : "
                        + ", ".join(ext)})
    if "10:00" <= maintenant.strftime("%H:%M") < "18:00" and any(st(g) == "on" for g in GUIRLANDE):
        out.append({"cle": "guirlande_aelia", "texte": "La guirlande d'Aelia est encore allumée en journée"})
    b = _num(st(TABLETTE))
    if b is not None and b < SEUIL_TABLETTE:
        out.append({"cle": "tablette", "texte": f"Tablette du salon presque vide ({int(b)} %)"})
    return out


class AgentAnalyse:
    def __init__(self, core, notifier: str) -> None:
        self.core = core
        self.notifier = notifier
        self.dernier: dict | None = None

    def _cles_du_jour(self, jour: str) -> list[str]:
        try:
            d = json.loads(self.core.memoire.meta("analyse_cles") or "{}")
            return d.get(jour, []) if isinstance(d, dict) else []
        except Exception:
            return []

    async def publier(self) -> None:
        d = self.dernier or {}
        try:
            await self.core.appel("POST", f"/states/{CAPTEUR}", {
                "state": "anomalie" if d.get("anomalies") else ("ok" if d else "inconnu"),
                "attributes": {"friendly_name": "ADRIA Core — analyse (anomalies)", "icon": "mdi:home-search",
                               "anomalies": d.get("anomalies", []), "notifiees": d.get("notifiees", []),
                               "quand": d.get("quand"), "essai": d.get("essai", False)}})
        except Exception as e:
            LOG.warning("Etat analyse non publié : %s", e)

    async def analyser(self, data: dict | None = None) -> dict:
        essai = str((data or {}).get("essai")).lower() == "true"
        maintenant = datetime.now(FUSEAU)
        anomalies = detecter(await self.core.appel("GET", "/states"), maintenant)
        jour = maintenant.date().isoformat()
        deja = self._cles_du_jour(jour)
        nouvelles = [a for a in anomalies if a["cle"] not in deja]
        res = {"quand": maintenant.isoformat(timespec="seconds"), "anomalies": anomalies,
               "notifiees": [], "essai": essai}
        if nouvelles and not essai and await self.core.etat(INTERRUPTEUR) == "on":
            try:
                domaine, service = self.notifier.split(".", 1)
                await self.core.appel("POST", f"/services/{domaine}/{service}", {
                    "title": "🔎 ADRIA : anomalie", "message": "\n".join(a["texte"] for a in nouvelles),
                    "data": {"tag": "adria_analyse", "channel": "ADRIA analyse"}})
                res["notifiees"] = [a["cle"] for a in nouvelles]
                self.core.memoire.meta("analyse_cles", json.dumps({jour: deja + res["notifiees"]}))
            except Exception as e:
                LOG.warning("Notification analyse : %s", e)
        self.dernier = res
        await self.publier()
        LOG.info("Analyse : %d anomalie(s), notifiées %s", len(anomalies), res["notifiees"])
        return res
