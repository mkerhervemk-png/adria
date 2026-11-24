"""Agent Auto-amélioration, lot AA1 (24/11/2026, validé par Mael) : UN seul bilan du dimanche.

Avant : 2 notifications 0 IA le dimanche (19h30 « Bilan confiance » v25, 19h40 « Bilan qualité »).
Maintenant : ces deux automatisations HA calculent toujours leur texte (rien ne change dans les mandats,
le frein v25 et les compteurs), mais l'envoient au Core par l'événement `adria_core_bilan`
{partie: confiance|qualite, titre, texte} au lieu de notifier. Le Core ajoute sa propre partie
(erreurs ouvertes du journal E1, échecs du routeur IA sur 7 j, anomalies AN1) et envoie à 19h45
UNE seule notification « 🧠 ADRIA — bilan de la semaine ». 0 IA.

- Interrupteur : input_boolean.adria_core_bilan. OFF -> les automatisations HA renotifient elles-mêmes.
- Si une partie HA manque à 19h45 (HA lent), elle est signalée « non reçue » ; rien n'est perdu
  côté HA (todo et compteurs restent la source).
- La revue S1 (propositions Oui/Non, mandats) n'est PAS touchée.
"""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

LOG = logging.getLogger("adria_core.bilan")

INTERRUPTEUR = "input_boolean.adria_core_bilan"
FUSEAU = ZoneInfo("Europe/Paris")
HEURE = "19:45"
PARTIES = {"confiance": "🤝 Confiance et mandats", "qualite": "🎙️ Qualité de la voix"}


def partie_core(erreurs: list[dict], echecs: dict, anomalies: int | None) -> str:
    lignes = []
    if erreurs:
        lignes.append(f"Erreurs encore ouvertes : {len(erreurs)} — "
                      + " ; ".join((e.get("erreur") or "?")[:60] for e in erreurs[:3]))
    else:
        lignes.append("Erreurs encore ouvertes : aucune.")
    tot = {m: sum(v.values()) for m, v in (echecs or {}).items()}
    if tot:
        lignes.append("Échecs des IA sur 7 j : " + ", ".join(f"{m} {n}" for m, n in sorted(tot.items())))
    if anomalies:
        lignes.append(f"Anomalies maison au dernier contrôle : {anomalies}")
    return "\n".join(lignes)


def composer(parties: dict, core: str) -> str:
    blocs = []
    for cle, titre in PARTIES.items():
        p = parties.get(cle)
        blocs.append(f"{titre}\n{p['texte'].strip()}" if p and p.get("texte") else f"{titre}\n(non reçue)")
    blocs.append(f"⚙️ Le Core\n{core}")
    return "\n\n".join(blocs)


class AgentBilan:
    def __init__(self, core, notifier: str) -> None:
        self.core = core
        self.notifier = notifier
        self.dernier: dict | None = None

    def _semaine(self) -> str:
        return datetime.now(FUSEAU).strftime("%G-W%V")

    def _parties(self) -> dict:
        try:
            d = json.loads(self.core.memoire.meta("bilan_parties") or "{}")
            return d.get("parties", {}) if d.get("semaine") == self._semaine() else {}
        except Exception:
            return {}

    def recevoir(self, data: dict) -> None:
        cle = str((data or {}).get("partie") or "")
        if cle not in PARTIES:
            return
        parties = self._parties()
        parties[cle] = {"texte": str(data.get("texte") or "")[:3000], "titre": str(data.get("titre") or "")}
        self.core.memoire.meta("bilan_parties", json.dumps({"semaine": self._semaine(), "parties": parties},
                                                           ensure_ascii=False))
        LOG.info("Bilan : partie %s reçue", cle)

    async def envoyer(self, data: dict | None = None) -> dict:
        essai = str((data or {}).get("essai")).lower() == "true"
        if not essai and datetime.now(FUSEAU).weekday() != 6:
            return {"envoye": False, "raison": "pas dimanche"}
        c = self.core
        core = partie_core(c.evolution.lister("ouverte", 20), c.memoire.stats_ia_echecs(7 * 86400),
                           len((getattr(c, "analyse", None) and (c.analyse.dernier or {}).get("anomalies")) or []))
        texte = composer(self._parties(), core)
        if getattr(c, "notifs", None):  # L5 (24/11) : ce que le Core a observé des notifications
            texte += "\n\n🔔 Notifications\n" + c.notifs.bilan()
        res = {"quand": datetime.now(FUSEAU).isoformat(timespec="seconds"), "texte": texte, "envoye": False,
               "essai": essai}
        if not essai and await c.etat(INTERRUPTEUR) == "on":
            domaine, service = self.notifier.split(".", 1)
            await c.appel("POST", f"/services/{domaine}/{service}", {
                "title": "🧠 ADRIA — bilan de la semaine", "message": texte,
                "data": {"tag": "adria_bilan_semaine", "group": "adria_bilans"}})
            res["envoye"] = True
        self.dernier = res
        LOG.info("Bilan du dimanche : envoyé=%s", res["envoye"])
        return res
