"""L3 « Pas envahissante » (observation) + L5 « Elle apprend ce qui t'intéresse » (24/11/2026,
validé par Mael : « Oui de L1 à L5 »).

L3 — le Core écoute l'événement HA `call_service` et note chaque notification envoyée au téléphone de
Mael (notify.mobile_app_telephone_mael_*) : heure, titre, tag. **Observation seule pendant 7 jours :
rien n'est bloqué, retardé ni regroupé.** Il calcule ce qu'il aurait fait : doublons (même titre en
moins de 30 min), notifications pendant les heures calmes (22h–07h), au-delà de 3 messages
spontanés par jour. Publié dans sensor.adria_core_notifications.

L5 — le bilan unique du dimanche (AA1) reçoit une partie « Notifications » : les titres les plus
fréquents de la semaine, les doublons, et une proposition de réduction. Rien n'est appliqué sans
l'accord de Mael (il le dit à Claude ou à ADRIA).
Interrupteur : input_boolean.adria_core_notifications (OFF = plus d'enregistrement).
"""

import json
import logging
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LOG = logging.getLogger("adria_core.notifs")

INTERRUPTEUR = "input_boolean.adria_core_notifications"
CAPTEUR = "sensor.adria_core_notifications"
FUSEAU = ZoneInfo("Europe/Paris")
CIBLE = "mobile_app_telephone_mael"
DOUBLON_S = 1800
BUDGET_JOUR = 3
GARDE_JOURS = 7
URGENT = ("intrusion", "alarme", "incendie", "sécurité", "securite", "urgence", "🚨")


def calme(h: datetime) -> bool:
    return h.hour >= 22 or h.hour < 7


def urgent(titre: str) -> bool:
    t = (titre or "").lower()
    return any(m in t for m in URGENT)


def analyser(notes: list[dict], maintenant: datetime) -> dict:
    """notes = [{t: iso, titre, tag}] -> statistiques d'observation (0 IA)."""
    jour = maintenant.date().isoformat()
    auj = [n for n in notes if n["t"][:10] == jour]
    doublons, vus = 0, {}
    for n in sorted(notes, key=lambda n: n["t"]):
        k = (n.get("titre") or "")[:60]
        t = datetime.fromisoformat(n["t"])
        if k in vus and (t - vus[k]).total_seconds() < DOUBLON_S:
            doublons += 1
        vus[k] = t
    spont = [n for n in auj if not urgent(n.get("titre", ""))]
    top = Counter((n.get("titre") or "?")[:50] for n in notes).most_common(5)
    return {"aujourdhui": len(auj), "heures_calmes_aujourdhui": sum(1 for n in auj if calme(datetime.fromisoformat(n["t"]))),
            "au_dela_du_budget_aujourdhui": max(0, len(spont) - BUDGET_JOUR), "doublons_7j": doublons,
            "total_7j": len(notes), "top_7j": [{"titre": t, "fois": c} for t, c in top]}


def partie_bilan(stats: dict) -> str:
    if not stats.get("total_7j"):
        return "Aucune notification observée cette semaine."
    l = [f"{stats['total_7j']} notifications en 7 jours, dont {stats['doublons_7j']} doublon(s) (même titre en moins de 30 min)."]
    if stats.get("top_7j"):
        l.append("Les plus fréquentes : " + ", ".join(f"{x['titre']} ({x['fois']})" for x in stats["top_7j"][:3]) + ".")
    gros = [x for x in stats.get("top_7j", []) if x["fois"] >= 7 and not urgent(x["titre"])]
    if gros:
        l.append(f"Proposition : regrouper « {gros[0]['titre']} » dans le résumé du soir. Dis-le à Claude ou à ADRIA si tu es d'accord.")
    return "\n".join(l)


class AgentNotifs:
    def __init__(self, core) -> None:
        self.core = core
        self.stats: dict = {}

    def _notes(self) -> list[dict]:
        try:
            return json.loads(self.core.memoire.meta("notifs_observees") or "[]")
        except Exception:
            return []

    async def recevoir(self, data: dict) -> None:
        """Événement call_service : ne garde que les notifications au téléphone de Mael."""
        if (data or {}).get("domain") != "notify" or not str(data.get("service", "")).startswith(CIBLE):
            return
        if await self.core.etat(INTERRUPTEUR) != "on":
            return
        sd = data.get("service_data") or {}
        maintenant = datetime.now(FUSEAU)
        limite = (maintenant - timedelta(days=GARDE_JOURS)).isoformat()
        notes = [n for n in self._notes() if n["t"] >= limite][-800:]
        notes.append({"t": maintenant.isoformat(timespec="seconds"), "titre": str(sd.get("title") or "")[:80],
                      "tag": str((sd.get("data") or {}).get("tag") or "")[:40]})
        self.core.memoire.meta("notifs_observees", json.dumps(notes, ensure_ascii=False))
        self.stats = analyser(notes, maintenant)
        await self.publier()

    async def publier(self) -> None:
        if not self.stats:
            self.stats = analyser(self._notes(), datetime.now(FUSEAU))
        s = self.stats
        await self.core.publier_capteur(CAPTEUR, {
            "state": s.get("aujourdhui", 0),
            "attributes": {"friendly_name": "ADRIA Core — notifications (observation)", "icon": "mdi:bell-badge-outline",
                           "unit_of_measurement": "aujourd'hui", "mode": "observation (rien n'est bloqué)", **s}})

    def bilan(self) -> str:
        return partie_bilan(analyser(self._notes(), datetime.now(FUSEAU)))
