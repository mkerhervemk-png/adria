"""Agent Famille, lot F1 « lecture » (24/11/2026, validé par Mael) : « HA détecte, le Core réfléchit ».

Avant, chaque matin d'école Mael recevait 4 notifications :
06h30 « EDT Iwan — fin des cours », 06h30 « EDT Ryan — fin des cours », 07h00 « EDT Iwan aujourd'hui »,
07h00 « EDT Ryan aujourd'hui ».
Maintenant :
- les deux lectures HA de 06h30 continuent de régler leurs aides (fin de cours, « devrait être à
  l'école », heures d'anticipation et d'alerte de retard) : elles restent dans HA car les alertes de
  retour en dépendent. Elles ne notifient plus la fin des cours quand input_boolean.adria_core_famille
  est ON (l'alerte « EDT illisible » de 07h30 reste) ;
- le Core envoie à 07h00 UNE notification « 🎒 Journée des enfants » (Iwan puis Ryan : horaires,
  fin des cours retenue par HA, cours, annulés / modifiés, malade) et la garde en mémoire (source
  `famille`) pour « Mon passé » ;
- l'automatisation HA de 07h00 (fusion EDT) ne part plus que si l'interrupteur est OFF ou si le Core
  ne donne plus signe de vie (secours).
Rien en vacances scolaires ni le week-end. 0 IA. Ne pilote rien.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

LOG = logging.getLogger("adria_core.famille")

INTERRUPTEUR = "input_boolean.adria_core_famille"
CAPTEUR = "sensor.adria_core_famille"
FUSEAU = ZoneInfo("Europe/Paris")
HEURE = "07:00"
VACANCES = "calendar.vacances_scolaires_zone_b"
ENFANTS = (("Iwan", "iwan", "input_text.edt_iwan_fin_cours_aujourd_hui", "input_boolean.iwan_malade"),
           ("Ryan", "ryan", "input_text.edt_ryan_fin_cours_aujourd_hui", None))
STAGE_RYAN = "input_boolean.ryan_en_stage"
INCONNU = ("unknown", "unavailable", "none", "", None)


def journee(etats: list[dict], maintenant: datetime, stage: dict | None = None) -> dict:
    """Etats HA -> {ecole: bool, lignes: [...], texte}. Sans IA."""
    par = {e["entity_id"]: e for e in etats}
    st = lambda i: (par.get(i) or {}).get("state")  # noqa: E731
    at = lambda i: (par.get(i) or {}).get("attributes") or {}  # noqa: E731
    if maintenant.weekday() >= 5:
        return {"ecole": False, "lignes": [], "texte": "week-end"}
    if st(VACANCES) == "on":
        return {"ecole": False, "lignes": [], "texte": "vacances scolaires"}
    blocs = []
    for nom, cle, fin_aide, malade in ENFANTS:
        if malade and st(malade) == "on":
            blocs.append(f"{nom} : malade aujourd'hui (pas d'école).")
            continue
        if cle == "ryan" and st(STAGE_RYAN) == "on":  # 24/11 : pendant le stage, jamais Ecole Directe
            creneaux = sorted((stage or {}).get("ryan", []))
            if creneaux:
                blocs.append("Ryan : stage " + " / ".join(f"{d}–{f}" for d, f in creneaux)
                             + (f" · {stage.get('lieu')}" if stage.get("lieu") else ""))
            else:
                blocs.append("Ryan : stage, pas de créneau dans son agenda aujourd'hui.")
            continue
        s = f"sensor.ed_{cle}_kerherve_emploi_du_temps_aujourd_hui"
        cours = at(s).get("Emploi du temps") or [] if st(s) not in INCONNU else []
        fin = st(fin_aide)
        actifs = [c for c in cours if not c.get("is_annule")]
        if cours and not actifs:
            blocs.append(f"{nom} : tous les cours sont annulés.")
            continue
        if not cours:
            blocs.append(f"{nom} : emploi du temps illisible"
                         + (f", fin des cours retenue {fin}." if fin not in INCONNU and fin != "inconnu" else "."))
            continue
        debut = at(s).get("Journée début") or actifs[0].get("start_time", "?")
        fin_txt = fin if fin not in INCONNU and fin not in ("inconnu", "aucun") else at(s).get("Journée fin", "?")
        tete = f"{nom} : {debut} → {fin_txt} · {len(actifs)} cours"
        details = []
        for c in cours:
            m = (c.get("lesson") or "").strip().title() or f"créneau sans matière (jusqu'à {c.get('end_time', '?')})"
            if c.get("is_annule"):
                details.append(f"{c.get('start_time', '')} {m} ❌ annulé")
            elif c.get("is_modifie"):
                details.append(f"{c.get('start_time', '')} {m} ⚠️ modifié")
            else:
                details.append(f"{c.get('start_time', '')} {m}")
        blocs.append(tete + "\n" + "\n".join(details))
    return {"ecole": True, "lignes": blocs, "texte": "\n\n".join(blocs)}


class AgentFamille:
    def __init__(self, core, notifier: str) -> None:
        self.core = core
        self.notifier = notifier
        self.dernier: dict | None = None

    async def publier(self) -> None:
        d = self.dernier or {}
        try:
            await self.core.appel("POST", f"/states/{CAPTEUR}", {
                "state": "école" if d.get("ecole") else ("pas d'école" if d else "inconnu"),
                "attributes": {"friendly_name": "ADRIA Core — journée des enfants", "icon": "mdi:school",
                               "texte": (d.get("texte") or "")[:1500], "quand": d.get("quand"),
                               "notifie": d.get("notifie", False), "essai": d.get("essai", False)}})
        except Exception as e:
            LOG.warning("Etat famille non publié : %s", e)

    async def _stage(self, maintenant: datetime) -> dict:
        """Créneaux « Stage » du jour dans calendar.ryan (24/11 : stage de Ryan, Ecole Directe ignoré)."""
        try:
            debut = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
            rep = await self.core.appel("POST", "/services/calendar/get_events?return_response", {
                "entity_id": "calendar.ryan", "start_date_time": debut.isoformat(),
                "end_date_time": debut.replace(hour=23, minute=59).isoformat()})
            ev = ((rep or {}).get("service_response") or {}).get("calendar.ryan", {}).get("events", [])
        except Exception:
            return {}
        ev = [e for e in ev if "stage" in (e.get("summary") or "").lower() and "T" in str(e.get("start"))]
        lieu = next((e.get("location") for e in ev if e.get("location")), "")
        return {"ryan": [(str(e["start"])[11:16], str(e["end"])[11:16]) for e in ev],
                "lieu": lieu.split(",")[0] if lieu else ""}

    async def matin(self, data: dict | None = None) -> dict:
        essai = str((data or {}).get("essai")).lower() == "true"
        maintenant = datetime.now(FUSEAU)
        res = journee(await self.core.appel("GET", "/states"), maintenant, await self._stage(maintenant))
        res.update(quand=maintenant.isoformat(timespec="seconds"), notifie=False, essai=essai)
        if res["ecole"] and not essai and await self.core.etat(INTERRUPTEUR) == "on":
            jour = maintenant.date().isoformat()
            domaine, service = self.notifier.split(".", 1)
            await self.core.appel("POST", f"/services/{domaine}/{service}", {
                "title": "🎒 Journée des enfants", "message": res["texte"],
                "data": {"tag": "adria_journee_enfants", "group": "ecole_directe",
                         "notification_icon": "mdi:timetable"}})
            res["notifie"] = True
            self.core.memoire.ajouter("famille", [{
                "uid": f"ecole-{jour}", "summary": f"Journée d'école du {jour} : " + res["texte"].replace("\n", " ")[:900],
                "description": "agent Famille F1, 0 IA", "date": jour}], maintenant)
        self.dernier = res
        await self.publier()
        LOG.info("Famille (matin) : école=%s, notifié=%s", res["ecole"], res["notifie"])
        return res
