"""Agent Maintenance (lot MA1, 19/11/2026) : « HA détecte, le Core réfléchit ».

- Les vigiles de Home Assistant (étiquette agent_maintenance, 0 IA) restent en place : ils détectent.
- Le Core lit chaque matin à 06h30 ce qu'ils ont produit + l'état de la maison, sans IA (0 token),
  et en tire UN état : ok / surveiller / probleme, publié dans sensor.adria_core_maintenance.
- Une seule notification, et seulement si un point est NOUVEAU depuis la veille (ou réglé).
- Remplace l'ancien diagnostic « avatar_maint » (Gemini), éteint depuis le 22/10.
- Interrupteur : input_boolean.adria_core_maintenance (OFF = calcul visible dans le capteur,
  mais aucune notification). Événement adria_core_maintenance {essai: true} = calcul sans notification.
- N'écrit rien dans HA en dehors de son capteur et de sa notification ; ne corrige rien tout seul.
"""

import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

LOG = logging.getLogger("adria_core.maintenance")

INTERRUPTEUR = "input_boolean.adria_core_maintenance"
CAPTEUR = "sensor.adria_core_maintenance"
FUSEAU = ZoneInfo("Europe/Paris")
GRAVITE = {"ok": 0, "surveiller": 1, "probleme": 2}

# appareils dont la batterie est déjà suivie ailleurs (téléphones, tablette, voiture)
BATTERIE_EXCLUE = re.compile(r"telephone|s25_|sm_t720|tablette|inster|javier", re.I)
SEUIL_BATTERIE = 20
MARGE_INDISPO = 15          # alerte si + de 15 entités indisponibles au-dessus du max des 7 derniers jours
SEUIL_COUPURE_H = 0.5       # coupures réseau du PC HA sur 24 h (heures)
SEUIL_SANTE_IA = 70


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _nom(e: dict) -> str:
    return (e.get("attributes") or {}).get("friendly_name") or e["entity_id"]


def _liste(noms: list[str], n: int = 3) -> str:
    noms = sorted(noms)
    return ", ".join(noms[:n]) + (f" (+{len(noms) - n})" if len(noms) > n else "")


def diagnostiquer(etats: list[dict], core: dict | None = None,
                  indispo_passes: list[int] | None = None) -> tuple[list[dict], int]:
    """Etats HA (GET /api/states) -> (points, nb d'entités indisponibles). Sans IA.

    Un point = {cle, niveau: surveiller|probleme, texte}. La clé est stable d'un jour à l'autre
    (sert à repérer ce qui est nouveau)."""
    core = core or {}
    par_id = {e["entity_id"]: e for e in etats}
    points: list[dict] = []

    def ajouter(cle, niveau, texte):
        points.append({"cle": cle, "niveau": niveau, "texte": texte})

    def etat(eid):
        return (par_id.get(eid) or {}).get("state")

    # 1. automatisations qui n'ont pas pu se charger
    ko = [_nom(e) for e in etats if e["entity_id"].startswith("automation.") and e["state"] == "unavailable"]
    if ko:
        ajouter("automatisations_ko", "probleme", f"{len(ko)} automatisation(s) refusée(s) par HA : {_liste(ko)}")

    # 2. entités indisponibles : seulement une hausse nette par rapport aux 7 derniers jours
    indispo = sum(1 for e in etats if e["state"] == "unavailable")
    passes = [n for n in (indispo_passes or []) if isinstance(n, int)][-7:]
    if passes and indispo > max(passes) + MARGE_INDISPO:
        ajouter("indisponibles", "surveiller",
                f"{indispo} entités indisponibles (max des 7 derniers jours : {max(passes)})")

    # 3. caméras (santé Frigate)
    cam = [_nom(e).replace(" Problème", "").replace(" problème", "") for e in etats
           if e["entity_id"].startswith("binary_sensor.frigate_sante_") and e["entity_id"].endswith("_probleme")
           and e["state"] == "on"]
    if cam:
        ajouter("cameras", "probleme", f"caméra(s) en défaut : {_liste(cam)}")

    # 4. piles des capteurs de la maison (hors téléphones, tablette, voiture)
    piles = []
    for e in etats:
        eid = e["entity_id"]
        a = e.get("attributes") or {}
        if not eid.startswith("sensor.") or BATTERIE_EXCLUE.search(eid):
            continue
        if a.get("device_class") != "battery" and not eid.endswith(("_batterie", "_battery")):
            continue
        v = _num(e["state"])
        if v is not None and v <= SEUIL_BATTERIE:
            piles.append(f"{_nom(e)} {int(v)} %")
    if piles:
        ajouter("piles", "surveiller", f"pile(s) faible(s) : {_liste(piles)}")

    # 5. mises à jour en attente
    maj = [_nom(e).replace(" Mise à jour", "") for e in etats
           if e["entity_id"].startswith("update.") and e["state"] == "on"]
    if maj:
        ajouter("mises_a_jour", "surveiller", f"{len(maj)} mise(s) à jour en attente : {_liste(maj)}")

    # 6. réseau du PC HA
    coupure = _num(etat("sensor.192_168_1_1_adria_duree_coupure_reseau_pc_ha_24h"))
    if coupure is not None and coupure >= SEUIL_COUPURE_H:
        ajouter("reseau", "surveiller", f"réseau du PC HA coupé {round(coupure * 60)} min sur 24 h")

    # 7. téléphones injoignables (relevé par la vigie HA)
    inj = (etat("input_text.adria_telephones_injoignables") or "").strip()
    if inj and inj not in ("unknown", "unavailable"):
        noms = [x.split("@")[0] for x in inj.split(",") if x.strip()]
        if noms:
            ajouter("telephones", "surveiller", f"téléphone(s) injoignable(s) : {_liste(noms)}")

    # 8. santé des IA
    sante = _num(etat("sensor.ia_health_score"))
    if sante is not None and sante < SEUIL_SANTE_IA:
        ajouter("sante_ia", "surveiller", f"santé des IA à {int(sante)}/100")

    # 9. le Core lui-même
    purge = core.get("derniere_purge") or {}
    if purge.get("erreurs"):
        ajouter("core_purge", "probleme", f"purge de nuit du Core en erreur ({len(purge['erreurs'])})")
    conso = core.get("derniere_consolidation") or {}
    if conso and conso.get("ok") is False:
        ajouter("core_consolidation", "surveiller",
                f"consolidation de nuit du Core en échec ({conso.get('raison') or '?'})")
    ia = core.get("stats_ia") or {}
    appels = sum(v.get("appels", 0) for v in ia.values())
    reussis = sum(v.get("ok", 0) for v in ia.values())
    if appels >= 10 and reussis / appels < 0.8:
        ajouter("core_ia", "surveiller", f"IA du Core : {reussis}/{appels} réponses réussies sur 24 h")

    return points, indispo


def niveau_global(points: list[dict]) -> str:
    n = max((GRAVITE[p["niveau"]] for p in points), default=0)
    return next(k for k, v in GRAVITE.items() if v == n)


def comparer(points: list[dict], cles_veille: list[str]) -> tuple[list[dict], list[str]]:
    """-> (points nouveaux, clés réglées depuis la veille)."""
    actuelles = {p["cle"] for p in points}
    nouveaux = [p for p in points if p["cle"] not in set(cles_veille)]
    regles = [c for c in cles_veille if c not in actuelles]
    return nouveaux, regles


LIBELLES = {"automatisations_ko": "automatisations", "indisponibles": "entités indisponibles",
            "cameras": "caméras", "piles": "piles", "mises_a_jour": "mises à jour", "reseau": "réseau",
            "telephones": "téléphones", "sante_ia": "santé des IA", "core_purge": "purge du Core",
            "core_consolidation": "consolidation du Core", "core_ia": "IA du Core"}


def message(nouveaux: list[dict], regles: list[str]) -> str:
    lignes = [("🔴 " if p["niveau"] == "probleme" else "🟠 ") + p["texte"] for p in nouveaux]
    if regles:
        lignes.append("✅ réglé : " + ", ".join(LIBELLES.get(c, c) for c in regles))
    return "\n".join(lignes)


class AgentMaintenance:
    def __init__(self, core, notifier: str) -> None:
        self.core = core
        self.notifier = notifier
        self.dernier: dict | None = None

    def _lire(self, cle: str, defaut):
        try:
            return json.loads(self.core.memoire.meta(cle) or "null") or defaut
        except Exception:
            return defaut

    def _ecrire(self, cle: str, valeur) -> None:
        self.core.memoire.meta(cle, json.dumps(valeur, ensure_ascii=False))

    async def publier(self) -> None:
        d = self.dernier or {}
        try:
            await self.core.appel("POST", f"/states/{CAPTEUR}", {
                "state": d.get("niveau", "inconnu"),
                "attributes": {"friendly_name": "ADRIA Core — maintenance", "icon": "mdi:stethoscope",
                               "points": d.get("points", []), "nouveaux": d.get("nouveaux", []),
                               "regles": d.get("regles", []), "indisponibles": d.get("indisponibles"),
                               "quand": d.get("quand"), "notifie": d.get("notifie", False),
                               "essai": d.get("essai", False)}})
        except Exception as e:
            LOG.warning("Etat maintenance non publié : %s", e)

    async def diagnostic(self, data: dict | None = None) -> dict:
        data = data or {}
        essai = str(data.get("essai")).lower() == "true"
        etats = await self.core.appel("GET", "/states")
        core = {"derniere_purge": self.core.derniere_purge,
                "derniere_consolidation": self.core.consolidation.resume(),
                "stats_ia": self.core.memoire.stats_ia()}
        passes = self._lire("maintenance_indispo", [])
        points, indispo = diagnostiquer(etats, core, passes)
        veille = self._lire("maintenance_cles", [])
        nouveaux, regles = comparer(points, veille)
        res = {"quand": datetime.now(FUSEAU).isoformat(timespec="seconds"), "niveau": niveau_global(points),
               "points": points, "nouveaux": [p["cle"] for p in nouveaux], "regles": regles,
               "indisponibles": indispo, "essai": essai, "notifie": False}
        if not essai:  # l'essai ne déplace pas la référence du lendemain
            self._ecrire("maintenance_cles", [p["cle"] for p in points])
            self._ecrire("maintenance_indispo", (passes + [indispo])[-7:])
            if (nouveaux or regles) and await self.core.etat(INTERRUPTEUR) == "on":
                try:
                    domaine, service = self.notifier.split(".", 1)
                    titre = {"probleme": "🔧 Maison : problème", "surveiller": "🔧 Maison : à surveiller",
                             "ok": "🔧 Maison : tout va bien"}[res["niveau"]]
                    await self.core.appel("POST", f"/services/{domaine}/{service}", {
                        "title": titre, "message": message(nouveaux, regles),
                        "data": {"tag": "adria_maintenance", "channel": "ADRIA maintenance"}})
                    res["notifie"] = True
                except Exception as e:
                    LOG.warning("Notification maintenance : %s", e)
        self.dernier = res
        await self.publier()
        LOG.info("Maintenance : %s, %d point(s), nouveaux %s, réglés %s",
                 res["niveau"], len(points), res["nouveaux"], regles)
        return res
