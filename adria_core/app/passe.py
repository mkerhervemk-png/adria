"""« Mon passé » (lot P1) : réunit dans la base du Core ce qui permet de répondre sur le passé, 0 IA.

Sources ajoutées (seulement si input_boolean.adria_core_passe est ON) :
- papiers : documents classés par A3 (/media/adria_documents/<Personne>/<Type>/<date>_<titre>) ;
  seulement personne, type, date et titre du fichier (jamais le contenu ni le résumé du courrier).
- agenda : événements passés des calendriers (hors vacances et jours fériés), 400 jours au 1er passage.
- adria_savoir : faits « retiens que… » ; la date du fait est lue dans la phrase (« en mars »),
  sinon c'est le jour où il a été retenu.
- historique : dates d'entretien (CT, assurance, révision, maintenances) et mails récents (expéditeur + objet,
  déjà filtrés par M1).

Rien n'est jamais effacé (règle du Core) : une ligne déjà vue est seulement revue.
"""

import fnmatch
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from periode import periode
from store import lignes_historique

LOG = logging.getLogger("adria_core.passe")

INTERRUPTEUR = "input_boolean.adria_core_passe"
RACINE_PAPIERS = "/media/adria_documents"
LISTE_SAVOIR = "todo.adria_savoir"
HISTORIQUE_PASSE = [
    "input_datetime.*derniere_revision", "input_datetime.ct_*", "input_datetime.*controle_technique",
    "input_datetime.*echeance_assurance", "input_datetime.prochaine_maintenance_*",
    "input_number.kilometrage_*_derniere_revision", "input_text.adria_mails_recents",
]
CALENDRIERS_EXCLUS = re.compile(r"vacances|ferie|holiday", re.I)
JOURS_AGENDA_DEPART = 400
NOM_PAPIER = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+?)(?:_\d{1,2})?$")
# Libellés lisibles des sources, pour que la réponse dise d'où vient l'information.
LIBELLES = {
    "agenda": "agenda", "papiers": "papier classé", "adria_savoir": "fait retenu",
    "historique": "historique maison", "adria_jarvis_episodes": "carnet du soir",
    "archive/episode": "carnet du soir", "adria_journal": "journal", "archive/journal": "journal",
}


def libelle(source: str) -> str:
    return LIBELLES.get(source, source.removeprefix("archive/").removeprefix("adria_"))


# ----------------------------------------------------------------------------- papiers
def lignes_papiers(racine: str = RACINE_PAPIERS) -> list[dict]:
    items = []
    if not os.path.isdir(racine):
        return items
    for personne in sorted(os.listdir(racine)):
        dp = os.path.join(racine, personne)
        if not os.path.isdir(dp):
            continue
        for typ in sorted(os.listdir(dp)):
            dt = os.path.join(dp, typ)
            if not os.path.isdir(dt):
                continue
            for f in sorted(os.listdir(dt)):
                m = NOM_PAPIER.match(os.path.splitext(f)[0])
                if not m:
                    continue
                titre = re.sub(r"[_\s]+", " ", m.group(2)).strip()
                items.append({"date": m.group(1),
                              "summary": f"Papier {typ} pour {personne} : {titre}",
                              "description": f"{personne}/{typ}/{f}"})
    return items


# ----------------------------------------------------------------------------- agenda
def _debut(ev: dict) -> tuple[str, str]:
    s = str(ev.get("start") or "")
    if "T" in s or " " in s:
        d = datetime.fromisoformat(s.replace(" ", "T").replace("Z", "+00:00"))
        return d.date().isoformat(), d.strftime("%H:%M")
    return s[:10], ""


def lignes_agenda(evenements: list[dict]) -> list[dict]:
    items = []
    for ev in evenements or []:
        titre = str(ev.get("summary") or "").strip()
        if not titre:
            continue
        try:
            jour, heure = _debut(ev)
            j = date.fromisoformat(jour)
        except ValueError:
            continue
        lieu = str(ev.get("location") or "").strip()
        items.append({"date": jour,
                      "summary": f"{j.strftime('%d/%m/%Y')}{(' ' + heure) if heure else ''} {titre}"
                                 + (f" ({lieu[:60]})" if lieu else ""),
                      "description": ""})
    return items


# ----------------------------------------------------------------------------- faits retenus
def lignes_savoir(items: list[dict], vu_le: date) -> list[dict]:
    """La date d'un fait = la période qu'il cite (« en mars »), sinon le jour où il a été retenu."""
    res = []
    for it in items or []:
        texte = str(it.get("summary") or "").strip()
        if not texte:
            continue
        per = periode(texte, vu_le)
        res.append({"summary": texte, "description": str(it.get("description") or ""),
                    "uid": it.get("uid"), "date": (per[0] if per else vu_le).isoformat()})
    return res


# ----------------------------------------------------------------------------- l'agent
class AgentPasse:
    """`core` = le Core : etat(), appel(), memoire."""

    def __init__(self, core) -> None:
        self.core = core
        self.dernier: dict | None = None

    async def actif(self) -> bool:
        return await self.core.etat(INTERRUPTEUR) == "on"

    async def importer(self, maintenant: datetime | None = None) -> dict:
        maintenant = maintenant or datetime.now()
        res: dict = {"quand": maintenant.isoformat(timespec="seconds"), "nouvelles": {}, "erreurs": []}
        if not await self.actif():
            res["actif"] = False
            self.dernier = res
            return res
        mem = self.core.memoire
        etapes = [("papiers", self._papiers), ("agenda", self._agenda),
                  ("adria_savoir", self._savoir), ("historique", self._historique)]
        for nom, f in etapes:
            try:
                res["nouvelles"][nom] = await f(mem, maintenant)
            except Exception as e:  # une source en panne ne bloque pas les autres
                res["erreurs"].append(f"{nom}: {str(e)[:100]}")
        LOG.info("Mon passé : %s", res)
        self.dernier = res
        return res

    async def _papiers(self, mem, maintenant) -> int:
        return mem.ajouter("papiers", lignes_papiers(RACINE_PAPIERS), maintenant)

    async def _agenda(self, mem, maintenant) -> int:
        dernier = mem.meta("passe_agenda_jusqu_a")
        debut = (date.fromisoformat(dernier) - timedelta(days=2) if dernier
                 else maintenant.date() - timedelta(days=JOURS_AGENDA_DEPART))
        fin = maintenant
        etats = await self.core.appel("GET", "/states")
        cals = [e["entity_id"] for e in etats if e["entity_id"].startswith("calendar.")
                and not CALENDRIERS_EXCLUS.search(e["entity_id"] + " "
                                                   + str(e.get("attributes", {}).get("friendly_name") or ""))]
        nouvelles = 0
        for cal in cals:
            rep = await self.core.appel("POST", "/services/calendar/get_events?return_response", {
                "entity_id": cal, "start_date_time": f"{debut.isoformat()} 00:00:00",
                "end_date_time": fin.strftime("%Y-%m-%d %H:%M:%S")}, delai=120)
            evs = ((rep.get("service_response") or {}).get(cal) or {}).get("events") or []
            nouvelles += mem.ajouter("agenda", lignes_agenda(evs), maintenant)
        mem.meta("passe_agenda_jusqu_a", maintenant.date().isoformat())
        return nouvelles

    async def _savoir(self, mem, maintenant) -> int:
        rep = await self.core.appel("POST", "/services/todo/get_items?return_response",
                                    {"entity_id": LISTE_SAVOIR})
        items = rep.get("service_response", {}).get(LISTE_SAVOIR, {}).get("items", [])
        # un fait déjà connu garde sa date d'origine (même empreinte : seulement revu)
        return mem.ajouter("adria_savoir", lignes_savoir(items, maintenant.date()), maintenant)

    async def _historique(self, mem, maintenant) -> int:
        etats = await self.core.appel("GET", "/states")
        noms = {e["entity_id"]: e.get("attributes", {}).get("friendly_name") or e["entity_id"] for e in etats}
        entites = sorted({eid for eid in noms for motif in HISTORIQUE_PASSE if fnmatch.fnmatch(eid, motif)})
        if not entites:
            return 0
        fin = datetime.now(timezone.utc)
        dernier = mem.meta("passe_historique_jusqu_a")
        debut = (datetime.fromisoformat(dernier) - timedelta(minutes=5) if dernier
                 else fin - timedelta(days=10))
        nouvelles = 0
        for i in range(0, len(entites), 20):
            serie = await self.core.appel(
                "GET", f"/history/period/{quote(debut.isoformat())}?end_time={quote(fin.isoformat())}"
                       f"&filter_entity_id={','.join(entites[i:i + 20])}&minimal_response&no_attributes")
            nouvelles += mem.ajouter("historique", lignes_historique(serie, noms, ZoneInfo("Europe/Paris")))
        mem.meta("passe_historique_jusqu_a", fin.isoformat())
        return nouvelles
