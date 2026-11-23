"""Agent Veille, lot V1 (23/11/2026, validé par Mael) : « HA détecte, le Core réfléchit ».

Les vigiles HA de l'étiquette agent_veille (météo, Tempo, gel, colis, actu, trafic) restent en place.
Le Core rassemble chaque matin à 06h45 (et sur l'événement adria_core_veille) ce qu'ils ont produit
en UN résumé « à retenir aujourd'hui », SANS IA (0 token) et SANS notification (choix de Mael en v22 :
réponses à la demande et briefings, pas d'alertes spontanées). Le résumé est publié dans
sensor.adria_core_veille et gardé dans la mémoire du Core (source « veille »), pour qu'ADRIA puisse
répondre « qu'est-ce qu'il y a aujourd'hui ? » et retrouver plus tard « quels jours rouges en janvier ? ».
Interrupteur : input_boolean.adria_core_veille (OFF = rien n'est gardé en mémoire, capteur seul).
Ne jamais lire la couleur Tempo de demain (règle Mael 06/10).
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

LOG = logging.getLogger("adria_core.veille")

INTERRUPTEUR = "input_boolean.adria_core_veille"
CAPTEUR = "sensor.adria_core_veille"
FUSEAU = ZoneInfo("Europe/Paris")
ALERTE = "sensor.meteo_france_alert_for_department_22_22_weather_alert"
TEMPO = "input_select.tempo_couleur_du_jour"
COLIS = "todo.colis"
OUVRE = "binary_sensor.workday_sensor"
METEO = "weather.meteo_france_forecast_for_city_saint_brieuc_bretagne_22_fr_saint_brieuc"
INCONNU = ("unknown", "unavailable", "", None)


def resumer(etats: list[dict]) -> dict:
    """Etats HA -> {points: [...], texte}. Sans IA."""
    par = {e["entity_id"]: e for e in etats}
    st = lambda i: (par.get(i) or {}).get("state")  # noqa: E731
    at = lambda i: (par.get(i) or {}).get("attributes") or {}  # noqa: E731
    points = []
    t = st(TEMPO)
    if t not in INCONNU:
        points.append(f"Tempo {t}" + (" : éviter les gros consommateurs" if t == "rouge" else ""))
    a = st(ALERTE)
    if a not in INCONNU and str(a).lower() != "vert":
        risques = [k for k, v in at(ALERTE).items() if isinstance(v, str) and v.lower() in ("jaune", "orange", "rouge")]
        points.append(f"Vigilance Météo-France {a}" + (f" ({', '.join(risques)})" if risques else ""))
    m = st(METEO)
    if m not in INCONNU:
        temp = at(METEO).get("temperature")
        points.append(f"Météo : {m}" + (f", {temp} °C" if temp is not None else ""))
    c = st(COLIS)
    try:
        if c not in INCONNU and int(float(c)) > 0:
            points.append(f"{int(float(c))} colis en attente")
    except (TypeError, ValueError):
        pass
    if st(OUVRE) == "off":
        points.append("Jour non ouvré (week-end ou férié)")
    return {"points": points, "texte": " ; ".join(points) or "rien de particulier"}


class AgentVeille:
    def __init__(self, core) -> None:
        self.core = core
        self.dernier: dict | None = None

    async def publier(self) -> None:
        d = self.dernier or {}
        try:
            await self.core.appel("POST", f"/states/{CAPTEUR}", {
                "state": (d.get("texte") or "inconnu")[:250],
                "attributes": {"friendly_name": "ADRIA Core — veille du jour", "icon": "mdi:radar",
                               "points": d.get("points", []), "quand": d.get("quand"),
                               "garde_en_memoire": d.get("garde", False)}})
        except Exception as e:
            LOG.warning("Etat veille non publié : %s", e)

    async def veiller(self, data: dict | None = None) -> dict:
        maintenant = datetime.now(FUSEAU)
        res = resumer(await self.core.appel("GET", "/states"))
        res.update(quand=maintenant.isoformat(timespec="seconds"), garde=False)
        if await self.core.etat(INTERRUPTEUR) == "on":
            jour = maintenant.date().isoformat()
            self.core.memoire.ajouter("veille", [{"uid": f"veille-{jour}", "summary": f"Veille du {jour} : {res['texte']}",
                                                  "description": "agent Veille, 0 IA", "date": jour}], maintenant)
            res["garde"] = True
        self.dernier = res
        await self.publier()
        LOG.info("Veille : %s", res["texte"])
        return res
