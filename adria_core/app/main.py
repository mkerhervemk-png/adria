"""ADRIA Core v0 : mémoire longue d'ADRIA, sans IA.

- Recopie chaque nuit les listes ADRIA de Home Assistant (rien n'est effacé).
- Répond aux recherches envoyées par l'événement `adria_core_recherche`
  avec l'événement `adria_core_resultat`.
- Publie son état dans `sensor.adria_core`.

Facultative : si l'appli est arrêtée, ADRIA fonctionne comme avant.
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

import aiohttp

from store import Memoire

LOG = logging.getLogger("adria_core")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
API = "http://supervisor/core/api"
WS = "ws://supervisor/core/websocket"
OPTIONS = "/data/options.json"
BASE = "/data/adria_core.db"
VERSION = "0.1.0"


def lire_options() -> dict:
    try:
        with open(OPTIONS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class Core:
    def __init__(self) -> None:
        opts = lire_options()
        self.listes: list[str] = opts.get("listes") or [
            "todo.adria_journal",
            "todo.adria_memoire",
            "todo.adria_jarvis_episodes",
            "todo.adria_memoire_perso",
            "todo.adria_souvenirs",
            "todo.adria_journal_interieur",
            "todo.adria_observations",
        ]
        self.heure_sync: str = opts.get("heure_sync") or "03:30"
        self.memoire = Memoire(BASE)
        self.derniere_sync: str | None = None
        self.derniere_erreur: str | None = None
        self.session: aiohttp.ClientSession | None = None

    @property
    def entetes(self) -> dict:
        return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

    async def appel(self, methode: str, chemin: str, donnees: dict | None = None) -> dict:
        assert self.session is not None
        async with self.session.request(
            methode, f"{API}{chemin}", headers=self.entetes, json=donnees,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            r.raise_for_status()
            return await r.json(content_type=None)

    async def synchroniser(self) -> None:
        nouvelles = 0
        erreurs = []
        for liste in self.listes:
            try:
                rep = await self.appel(
                    "POST", "/services/todo/get_items?return_response",
                    {"entity_id": liste},
                )
                items = rep.get("service_response", {}).get(liste, {}).get("items", [])
                nouvelles += self.memoire.ajouter(liste.removeprefix("todo."), items)
            except Exception as e:  # une liste absente ne bloque pas les autres
                erreurs.append(f"{liste}: {e}")
        self.derniere_sync = datetime.now().isoformat(timespec="seconds")
        self.derniere_erreur = "; ".join(erreurs)[:250] or None
        LOG.info("Synchronisation : %s lignes nouvelles, %s erreurs", nouvelles, len(erreurs))
        await self.publier_etat()

    async def publier_etat(self) -> None:
        s = self.memoire.stats()
        try:
            await self.appel("POST", "/states/sensor.adria_core", {
                "state": "erreur" if self.derniere_erreur else "ok",
                "attributes": {
                    "friendly_name": "ADRIA Core",
                    "icon": "mdi:database-search",
                    "lignes": s["lignes"],
                    "par_source": s["par_source"],
                    "depuis": s["depuis"],
                    "derniere_sync": self.derniere_sync,
                    "derniere_erreur": self.derniere_erreur,
                    "version": VERSION,
                },
            })
        except Exception as e:
            LOG.warning("Etat non publié : %s", e)

    async def repondre(self, data: dict) -> None:
        req_id = data.get("req_id", "")
        try:
            lignes = self.memoire.chercher(
                data.get("question", ""),
                int(data.get("n", 5)),
                data.get("exclure_sources") or [],
            )
            texte = " ; ".join(f"[{l['date']} {l['source']}] {l['texte']}" for l in lignes)
            resultat = {"req_id": req_id, "ok": True, "lignes": lignes, "texte": texte[:2000]}
        except Exception as e:
            resultat = {"req_id": req_id, "ok": False, "lignes": [], "texte": "", "erreur": str(e)[:200]}
        await self.appel("POST", "/events/adria_core_resultat", resultat)

    async def ecouter(self) -> None:
        """Ecoute les événements ; se reconnecte seul en cas de coupure."""
        attente = 5
        while True:
            try:
                assert self.session is not None
                async with self.session.ws_connect(WS, heartbeat=30) as ws:
                    await ws.receive_json()  # auth_required
                    await ws.send_json({"type": "auth", "access_token": TOKEN})
                    rep = await ws.receive_json()
                    if rep.get("type") != "auth_ok":
                        raise RuntimeError(f"auth refusée : {rep}")
                    for i, evt in enumerate(("adria_core_recherche", "adria_core_synchroniser"), 1):
                        await ws.send_json({"id": i, "type": "subscribe_events", "event_type": evt})
                    LOG.info("Connecté à Home Assistant")
                    attente = 5
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        m = json.loads(msg.data)
                        if m.get("type") != "event":
                            continue
                        e = m["event"]
                        if e["event_type"] == "adria_core_recherche":
                            asyncio.create_task(self.repondre(e.get("data", {})))
                        elif e["event_type"] == "adria_core_synchroniser":
                            asyncio.create_task(self.synchroniser())
            except Exception as e:
                LOG.warning("Connexion perdue (%s), nouvel essai dans %s s", e, attente)
            await asyncio.sleep(attente)
            attente = min(attente * 2, 300)

    async def planifier(self) -> None:
        while True:
            h, m = (int(x) for x in self.heure_sync.split(":"))
            maintenant = datetime.now()
            prochaine = maintenant.replace(hour=h, minute=m, second=0, microsecond=0)
            if prochaine <= maintenant:
                prochaine += timedelta(days=1)
            await asyncio.sleep((prochaine - maintenant).total_seconds())
            await self.synchroniser()

    async def demarrer(self) -> None:
        async with aiohttp.ClientSession() as session:
            self.session = session
            await asyncio.sleep(10)  # laisser Home Assistant finir de démarrer
            await self.synchroniser()
            await asyncio.gather(self.ecouter(), self.planifier())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(Core().demarrer())
