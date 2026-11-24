"""L1 « Vue d'ensemble » + L2 « Anticipation » (24/11/2026, validé par Mael : « Oui de L1 à L5 »).

L1 — toutes les 5 min, le Core tient un état compact du foyer (0 IA) : où est chacun, son prochain
événement d'agenda (24 h), quart de Mael, mode nuit, Tempo, météo, Inster, alarme, stage de Ryan,
vacances. Publié dans sensor.adria_core_monde (attribut `texte`, ~600 caractères) ; le script HA
script.adria_contexte_plus l'ajoute au contexte de chaque question vocale (ADRIA « omnisciente »
sans gros modèles Jinja).

L2 — à chaque calcul, le Core regarde les 24 h à venir et en tire des anticipations 0 IA :
Inster trop basse pour les trajets prévus, pluie à l'heure d'un départ, RDV d'enfant sans adresse
(pas de rappel de départ possible), anniversaire dans 7 jours. Publiées dans
sensor.adria_core_anticipations. **Jamais de notification** (pas intrusif) : elles sont lues par les
moments existants (résumé du soir 20h45, anticipation Jarvis au départ / retour) et à la voix.
Interrupteur : input_boolean.adria_core_monde (OFF = plus de calcul).
"""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LOG = logging.getLogger("adria_core.monde")

INTERRUPTEUR = "input_boolean.adria_core_monde"
CAPTEUR = "sensor.adria_core_monde"
CAPTEUR_ANT = "sensor.adria_core_anticipations"
FUSEAU = ZoneInfo("Europe/Paris")
METEO = "weather.meteo_france_forecast_for_city_saint_brieuc_bretagne_22_fr_saint_brieuc"
PERSONNES = ("mael", "vanessa", "ryan", "iwan", "aelia")
CALENDRIERS = tuple(f"calendar.{p}" for p in PERSONNES)
ANNIV = "calendar.anniversaires"
ENFANTS = ("iwan", "aelia")
INCONNU = ("unknown", "unavailable", "", None)
SEUIL_INSTER = 35          # % sous lequel on signale un trajet prévu
PLUIE_MM = 0.5
QUARTS = ("matin", "apres-midi", "après-midi", "nuit", "batarde", "bâtarde")


def _dt(s) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(s))
        return d if d.tzinfo else d.replace(tzinfo=FUSEAU)
    except (TypeError, ValueError):
        return None


def evenements(rep: dict) -> dict[str, list[dict]]:
    """Réponse calendar.get_events -> {personne: [événements horaires triés]}."""
    out: dict[str, list[dict]] = {}
    for cal, v in ((rep or {}).get("service_response") or {}).items():
        p = cal.split(".", 1)[1]
        evs = [e for e in (v or {}).get("events", []) if "T" in str(e.get("start"))]
        out[p] = sorted(evs, key=lambda e: str(e["start"]))
    return out


def vue(etats: list[dict], evs: dict, maintenant: datetime) -> dict:
    par = {e["entity_id"]: e for e in etats}
    st = lambda i: (par.get(i) or {}).get("state")  # noqa: E731
    at = lambda i: (par.get(i) or {}).get("attributes") or {}  # noqa: E731
    gens = {}
    for p in PERSONNES:
        ou = (st(f"sensor.adria_ou_{p}") or "?").split("|")[0]
        prochain = next((e for e in evs.get(p, []) if (_dt(e["start"]) or maintenant) > maintenant), None)
        pr = ""
        if prochain:
            d = _dt(prochain["start"])
            pr = f"{d.strftime('%a %H:%M') if d.date() != maintenant.date() else d.strftime('%H:%M')} {prochain.get('summary', '')[:40]}"
        gens[p] = {"ou": ou, "prochain": pr}
    maison = {
        "quart": st("input_select.quart_mael"), "mode_nuit": st("input_boolean.mode_nuit"),
        "tempo": st("input_select.tempo_couleur_du_jour"),
        "meteo": f"{st(METEO)} {at(METEO).get('temperature', '?')}°C",
        "inster": f"{st('sensor.inster_ev_battery_level')}% {('branchée' if st('binary_sensor.inster_ev_battery_plug') == 'on' else 'débranchée')}",
        "alarme": st("alarm_control_panel.home_maison_protegee_tsb"),
        "ryan_stage": st("input_boolean.ryan_en_stage") == "on",
        "vacances": st("calendar.vacances_scolaires_zone_b") == "on",
    }
    noms = {"mael": "Mael", "vanessa": "Vanessa", "ryan": "Ryan", "iwan": "Iwan", "aelia": "Aelia"}
    lignes = [f"{noms[p]}: {g['ou']}" + (f", prochain {g['prochain']}" if g["prochain"] else "") for p, g in gens.items()]
    texte = (f"[{maintenant.strftime('%H:%M')}] " + " | ".join(lignes)
             + f" || quart Mael {maison['quart']}, mode nuit {maison['mode_nuit']}, Tempo {maison['tempo']},"
             + f" météo {maison['meteo']}, Inster {maison['inster']}, alarme {maison['alarme']}"
             + (", Ryan en stage (pas d'École Directe)" if maison["ryan_stage"] else "")
             + (", vacances scolaires" if maison["vacances"] else ""))
    return {"gens": gens, "maison": maison, "texte": texte[:1200]}


def anticiper(etats: list[dict], evs: dict, anniv: list[dict], pluie: list[dict], maintenant: datetime) -> list[dict]:
    """-> [{cle, texte, quand}] 0 IA. Clé stable pour ne pas répéter."""
    par = {e["entity_id"]: e for e in etats}
    st = lambda i: (par.get(i) or {}).get("state")  # noqa: E731
    fin = maintenant + timedelta(hours=24)
    out = []
    a_venir = [(p, e) for p, l in evs.items() for e in l
               if maintenant < (_dt(e["start"]) or maintenant) <= fin
               and (e.get("summary") or "").strip().lower() not in QUARTS]
    # 1. Inster trop basse pour des trajets prévus
    try:
        soc = float(st("sensor.inster_ev_battery_level"))
    except (TypeError, ValueError):
        soc = None
    avec_lieu = [x for x in a_venir if (x[1].get("location") or "").strip()]
    if soc is not None and soc < SEUIL_INSTER and avec_lieu and st("binary_sensor.inster_ev_battery_plug") != "on":
        out.append({"cle": f"inster_{maintenant.date()}", "quand": "ce soir",
                    "texte": f"Inster à {int(soc)} % et non branchée, {len(avec_lieu)} trajet(s) prévu(s) d'ici demain : penser à la brancher"})
    # 2. Pluie à l'heure d'un départ
    for p, e in avec_lieu:
        d = _dt(e["start"])
        h = next((f for f in pluie if abs(((_dt(f.get("datetime")) or d) - d).total_seconds()) <= 3600), None)
        if h and float(h.get("precipitation") or 0) >= PLUIE_MM:
            out.append({"cle": f"pluie_{p}_{d.isoformat()[:16]}", "quand": d.strftime("%H:%M"),
                        "texte": f"Pluie prévue vers {d.strftime('%Hh%M')} pour {e.get('summary', '')[:30]} ({p.capitalize()}) : prévoir imperméable / un peu d'avance"})
            break
    # 3. RDV d'enfant sans adresse (pas de rappel de départ possible)
    for p, e in a_venir:
        if p in ENFANTS and not (e.get("location") or "").strip() and "cours" not in (e.get("summary") or "").lower():
            d = _dt(e["start"])
            out.append({"cle": f"adresse_{p}_{d.isoformat()[:16]}", "quand": d.strftime("%a %H:%M"),
                        "texte": f"{e.get('summary', '')[:30]} ({p.capitalize()}, {d.strftime('%a %Hh%M')}) n'a pas d'adresse : pas de rappel de départ"})
    # 4. Anniversaire dans les 7 jours
    for e in anniv:
        d = _dt(str(e.get("start")) + ("" if "T" in str(e.get("start")) else "T00:00:00"))
        if d and 0 <= (d.date() - maintenant.date()).days <= 7:
            j = (d.date() - maintenant.date()).days
            out.append({"cle": f"anniv_{e.get('summary', '')[:20]}_{d.date()}", "quand": d.strftime("%d/%m"),
                        "texte": f"{e.get('summary', '')[:50]} {'aujourd hui' if j == 0 else ('demain' if j == 1 else f'dans {j} jours')} ({d.strftime('%d/%m')})"})
    return out[:6]


class AgentMonde:
    def __init__(self, core) -> None:
        self.core = core
        self.dernier: dict | None = None
        self.anticipations: list[dict] = []
        self._evs: dict = {}
        self._anniv: list = []
        self._pluie: list = []
        self._cal_quand: datetime | None = None

    async def _agendas(self, maintenant: datetime) -> None:
        """Agendas et prévisions horaires : toutes les 15 min seulement."""
        if self._cal_quand and maintenant - self._cal_quand < timedelta(minutes=15):
            return
        fin = maintenant + timedelta(hours=26)
        try:
            rep = await self.core.appel("POST", "/services/calendar/get_events?return_response", {
                "entity_id": list(CALENDRIERS), "start_date_time": maintenant.isoformat(),
                "end_date_time": fin.isoformat()})
            self._evs = evenements(rep)
            rep = await self.core.appel("POST", "/services/calendar/get_events?return_response", {
                "entity_id": ANNIV, "start_date_time": maintenant.replace(hour=0, minute=0).isoformat(),
                "end_date_time": (maintenant + timedelta(days=8)).isoformat()})
            self._anniv = ((rep or {}).get("service_response") or {}).get(ANNIV, {}).get("events", [])
            rep = await self.core.appel("POST", "/services/weather/get_forecasts?return_response",
                                        {"entity_id": METEO, "type": "hourly"})
            self._pluie = ((rep or {}).get("service_response") or {}).get(METEO, {}).get("forecast", [])
            self._cal_quand = maintenant
        except Exception as e:
            LOG.warning("Agendas / météo non lus : %s", str(e)[:120])

    async def calculer(self, data: dict | None = None) -> dict | None:
        if await self.core.etat(INTERRUPTEUR) != "on" and not (data or {}).get("forcer"):
            return None
        maintenant = datetime.now(FUSEAU)
        await self._agendas(maintenant)
        etats = await self.core.appel("GET", "/states")
        self.dernier = vue(etats, self._evs, maintenant)
        self.anticipations = anticiper(etats, self._evs, self._anniv, self._pluie, maintenant)
        await self.publier()
        return self.dernier

    def texte_anticipations(self) -> str:
        return " ; ".join(a["texte"] for a in self.anticipations)

    async def publier(self) -> None:
        d = self.dernier or {}
        await self.core.publier_capteur(CAPTEUR, {
            "state": datetime.now(FUSEAU).strftime("%H:%M") if d else "inconnu",
            "attributes": {"friendly_name": "ADRIA Core — vue d'ensemble", "icon": "mdi:home-analytics",
                           "texte": d.get("texte", ""), "gens": d.get("gens", {}), "maison": d.get("maison", {})}})
        await self.core.publier_capteur(CAPTEUR_ANT, {
            "state": len(self.anticipations),
            "attributes": {"friendly_name": "ADRIA Core — anticipations (24 h)", "icon": "mdi:crystal-ball",
                           "unit_of_measurement": "points", "liste": self.anticipations,
                           "texte": self.texte_anticipations()[:1000]}})

    async def boucle(self) -> None:
        import asyncio
        while True:
            try:
                await self.calculer()
            except Exception:
                LOG.exception("Vue d'ensemble")
            await asyncio.sleep(300)
