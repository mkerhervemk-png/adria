"""ADRIA Core v0 : mémoire longue d'ADRIA, sans IA.

- Recopie chaque nuit les listes ADRIA de Home Assistant (rien n'est effacé).
- Répond aux recherches envoyées par l'événement `adria_core_recherche`
  avec l'événement `adria_core_resultat`.
- Publie son état dans `sensor.adria_core`.

Facultative : si l'appli est arrêtée, ADRIA fonctionne comme avant.
"""

import asyncio
import fnmatch
import glob
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp

from courriers import AgentCourriers
from formulaires import AgentFormulaires
from habitudes import AgentHabitudes
from memoire_agent import AgentMemoire
from passe import AgentPasse, libelle
from purge import LISTES as LISTES_PURGE, a_purger
from routeur import Routeur
from web import PageFormulaires
from store import Memoire, lignes_historique, lire_archive

LOG = logging.getLogger("adria_core")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
API = "http://supervisor/core/api"
WS = "ws://supervisor/core/websocket"
OPTIONS = "/data/options.json"
BASE = "/data/adria_core.db"
VERSION = "0.11.0"


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
        self.archive: str = opts.get("archive_dossier") or "/media/adria_memoire"
        self.historique: list[str] = opts.get("historique") or [
            "person.*", "sensor.adria_ou_*", "light.*", "media_player.lg_webos_tv_uj630v",
            "media_player.salon_decodeur_salon_media_player", "input_select.quart_mael",
            "input_boolean.mode_nuit",
        ]
        self.historique_jours: int = int(opts.get("historique_jours") or 14)
        self.ollama_url: str = opts.get("ollama_url") or "http://76e18fb5-ollama:11434"
        self.ollama_modele: str = opts.get("ollama_modele") or "llama3.2:3b"
        self.memoire = Memoire(BASE)
        self.routeur = Routeur(self)
        self.agent_memoire = AgentMemoire(self)
        self.passe = AgentPasse(self)
        self.heure_carnet: str = opts.get("heure_carnet") or "23:40"
        self.formulaires = AgentFormulaires(
            self, opts.get("formulaire_profil") or {},
            opts.get("formulaire_notifier") or "notify.mobile_app_telephone_mael_sm_s921b",
            opts.get("formulaire_foyer") or [],
            opts.get("onedrive_entree") or None)
        self.courriers = AgentCourriers(
            self, self.formulaires,
            opts.get("formulaire_notifier") or "notify.mobile_app_telephone_mael_sm_s921b",
            opts.get("courrier_calendrier") or "calendar.mael_adria",
            opts.get("onedrive_entree") or None)
        self.formulaires.autre = self.courriers
        self.habitudes = AgentHabitudes(
            self, opts.get("formulaire_notifier") or "notify.mobile_app_telephone_mael_sm_s921b")
        self.heure_habitudes: str = opts.get("habitudes_heure") or "17:30"
        self.heure_rappel: str = opts.get("courrier_heure_rappel") or "10:00"
        self.dernier_moteur: str | None = None
        self.derniere_sync: str | None = None
        self.derniere_erreur: str | None = None
        self.derniere_purge: dict | None = None
        self.session: aiohttp.ClientSession | None = None

    @property
    def entetes(self) -> dict:
        return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

    async def appel(self, methode: str, chemin: str, donnees: dict | None = None,
                    delai: float = 60) -> dict:
        assert self.session is not None
        async with self.session.request(
            methode, f"{API}{chemin}", headers=self.entetes, json=donnees,
            timeout=aiohttp.ClientTimeout(total=delai),
        ) as r:
            r.raise_for_status()
            return await r.json(content_type=None)

    # --- outils pour le routeur IA -------------------------------------------------
    async def etat(self, entity_id: str) -> str | None:
        try:
            return (await self.appel("GET", f"/states/{entity_id}")).get("state")
        except Exception:
            return None

    async def ai_task(self, entite: str, prompt: str, tache: str) -> str:
        rep = await self.appel(
            "POST", "/services/ai_task/generate_data?return_response",
            {"entity_id": entite, "instructions": prompt, "task_name": tache},
            delai=300,
        )
        data = (rep.get("service_response") or {}).get("data")
        return data if isinstance(data, str) else ("" if data is None else json.dumps(data, ensure_ascii=False))

    async def ai_task_vision(self, prompt: str, tache: str, medias: list[dict]) -> str:
        """Gemini avec images (formulaires). Respecte quota 100/j et ia_gemini_ko ; repli Gemini Lite."""
        try:
            appels = int(float(await self.etat("counter.avatar_gemini_calls_today") or 0))
        except ValueError:
            appels = 0
        entites = ["ai_task.google_ai_task_5", "ai_task.google_ai_task"]
        if appels >= 100 or await self.etat("input_boolean.ia_gemini_ko") == "on":
            entites = entites[1:]
        derniere = None
        for entite in entites:
            try:
                rep = await self.appel(
                    "POST", "/services/ai_task/generate_data?return_response",
                    {"entity_id": entite, "instructions": prompt, "task_name": tache,
                     "attachments": medias}, delai=180)
                if entite.endswith("_5"):
                    await self.incrementer("counter.avatar_gemini_calls_today")
                data = (rep.get("service_response") or {}).get("data")
                return data if isinstance(data, str) else json.dumps(data or {}, ensure_ascii=False)
            except Exception as e:
                derniere = e
                LOG.warning("Vision %s : %s", entite, str(e)[:150])
        raise RuntimeError(f"IA vision indisponible ({str(derniere)[:80]})")

    async def publier_etat_formulaires(self) -> None:
        d = self.formulaires.dernier or {}
        c = self.courriers.dernier or {}
        en_cours = self.formulaires.en_cours or self.courriers.en_cours
        try:
            await self.appel("POST", "/states/sensor.adria_core_formulaires", {
                "state": "en_cours" if en_cours else ("ok" if d.get("ok", True) and c.get("ok", True) else "erreur"),
                "attributes": {"friendly_name": "ADRIA Core — formulaires", "icon": "mdi:file-document-edit",
                               "dernier": d or None, "dernier_courrier": c or None, "version": VERSION}})
        except Exception as e:
            LOG.warning("Etat formulaires non publié : %s", e)

    async def ollama(self, prompt: str) -> str:
        assert self.session is not None
        try:
            async with self.session.post(
                f"{self.ollama_url}/api/generate",
                json={"model": self.ollama_modele, "prompt": prompt, "stream": False, "keep_alive": -1,
                      "think": False, "options": {"num_ctx": 4096}},
                timeout=aiohttp.ClientTimeout(total=300),
            ) as r:
                r.raise_for_status()
                return (await r.json()).get("response", "")
        except aiohttp.ClientError as e:  # Ollama injoignable en direct : passer par HA
            LOG.warning("Ollama direct indisponible (%s), passage par ai_task.adria_secours", e)
            return await self.ai_task("ai_task.adria_secours", prompt, "adria_core_ollama")

    async def statut(self, texte: str) -> None:
        try:
            await self.appel("POST", "/services/input_text/set_value", {
                "entity_id": "input_text.avatar_anti_boucle_status",
                "value": (datetime.now().strftime("%H:%M") + " | " + texte)[:255]})
        except Exception:
            pass

    async def incrementer(self, compteur: str) -> None:
        try:
            await self.appel("POST", "/services/counter/increment", {"entity_id": compteur})
        except Exception:
            pass

    async def noter(self, moteur: str, prof: str, ok: bool, duree: float, raison: str | None) -> None:
        self.memoire.noter_ia(moteur, prof, ok, duree, raison)
        if ok:
            self.dernier_moteur = moteur

    async def repondre_ia(self, data: dict) -> None:
        req_id = data.get("req_id", "")
        try:
            res = await self.routeur.demander(
                str(data.get("prompt") or ""),
                str(data.get("profondeur") or "rapide"),
                str(data.get("task_name") or "adria"),
                str(data.get("prompt_secours") or ""),
                str(data.get("force_gemini")).lower() == "true",
                str(data.get("seulement") or ""),
            )
        except Exception as e:
            LOG.exception("Routeur IA")
            res = {"ok": False, "data": "", "erreur": str(e)[:200]}
        res["req_id"] = req_id
        try:
            await self.appel("POST", "/events/adria_core_ia_resultat", res)
        except Exception as e:
            LOG.warning("Résultat IA non envoyé : %s", e)
        await self.publier_etat_ia()

    async def publier_etat_ia(self) -> None:
        try:
            await self.appel("POST", "/states/sensor.adria_core_ia", {
                "state": self.dernier_moteur or "aucun",
                "attributes": {
                    "friendly_name": "ADRIA Core — routeur IA",
                    "icon": "mdi:lan",
                    "moteurs_24h": self.memoire.stats_ia(86400),
                    "moteurs_7j": self.memoire.stats_ia(7 * 86400),
                    "coupe_circuit": self.routeur.cc.etat(),
                    "battement": datetime.now().isoformat(timespec="seconds"),
                    "version": VERSION,
                },
            })
        except Exception as e:
            LOG.warning("Etat IA non publié : %s", e)

    async def battement(self) -> None:
        """Signal de vie : sensor.adria_core_ia mis à jour chaque minute (lu par HA)."""
        while True:
            await self.publier_etat_ia()
            try:
                await self.habitudes.minute()
            except Exception as e:
                LOG.warning("Rappels d'habitude : %s", str(e)[:150])
            await asyncio.sleep(60)

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
        try:
            nouvelles += self.importer_archive()
        except Exception as e:
            erreurs.append(f"archive: {e}")
        try:
            nouvelles += await self.importer_historique()
        except Exception as e:
            erreurs.append(f"historique: {e}")
        try:
            p = await self.passe.importer()
            nouvelles += sum(p.get("nouvelles", {}).values())
            erreurs += p.get("erreurs", [])
        except Exception as e:
            erreurs.append(f"passe: {e}")
        self.derniere_sync = datetime.now().isoformat(timespec="seconds")
        self.derniere_erreur = "; ".join(erreurs)[:250] or None
        LOG.info("Synchronisation : %s lignes nouvelles, %s erreurs", nouvelles, len(erreurs))
        await self.publier_etat()

    async def nuit(self) -> None:
        """03h30 : recopie d'abord, puis purge des listes HA si l'interrupteur est ON."""
        await self.synchroniser()
        if await self.etat("input_boolean.adria_core_purge") == "on":
            await self.purger()
        try:  # calcul des habitudes (0 IA), visible dans sensor.adria_core_habitudes
            self.habitudes.calculer()
            await self.habitudes.publier()
        except Exception:
            LOG.exception("Habitudes")

    async def habitudes_dimanche(self, data: dict | None = None) -> None:
        data = data or {}
        try:
            await self.habitudes.proposer(essai=str(data.get("essai")).lower() == "true",
                                          forcer=str(data.get("forcer")).lower() == "true")
        except Exception:
            LOG.exception("Propositions d'habitudes")

    def _choisir(self, liste: str, source: str, items: list[dict], maintenant: datetime):
        self.memoire.ajouter(source, items)  # recopie juste avant : rien ne se perd
        choix = a_purger(liste, items, maintenant)
        return [i for i in choix if i.get("uid") and self.memoire.a_la_copie(source, i)], choix

    async def purger(self, data: dict | None = None) -> dict:
        """Lot M2b-1. Retire des listes HA seulement les lignes dont le Core a la copie.
        essai=True : compte sans rien retirer."""
        essai = str((data or {}).get("essai")).lower() == "true"
        maintenant = datetime.now()
        res: dict = {"quand": maintenant.isoformat(timespec="seconds"), "essai": essai,
                     "retirees": {}, "gardees_sans_copie": {}, "erreurs": [], "durees": {}}
        for liste in LISTES_PURGE:
            source = liste.removeprefix("todo.")
            t0 = time.monotonic()
            try:
                rep = await self.appel("POST", "/services/todo/get_items?return_response",
                                       {"entity_id": liste})
                items = rep.get("service_response", {}).get(liste, {}).get("items", [])
                ok, choix = self._choisir(liste, source, items, maintenant)
                res["durees"][source] = round(time.monotonic() - t0, 1)
                sans = len(choix) - len(ok)
                if ok and not essai:
                    for k in range(0, len(ok), 50):
                        await self.appel("POST", "/services/todo/remove_item", {
                            "entity_id": liste, "item": [i["uid"] for i in ok[k:k + 50]]})
                if ok:
                    res["retirees"][source] = len(ok)
                if sans:
                    res["gardees_sans_copie"][source] = sans
            except Exception as e:
                res["erreurs"].append(f"{source}: {str(e)[:120]}")
        LOG.info("Purge (%s) : %s", "essai" if essai else "réelle", res)
        self.derniere_purge = res
        try:
            await self.appel("POST", "/events/adria_core_purge_resultat", res)
        except Exception:
            pass
        await self.publier_etat()
        return res

    def importer_archive(self) -> int:
        """Relit l'archive /media/adria_memoire (*.md) ; les doublons sont ignorés."""
        par_source: dict[str, list[dict]] = {}
        for f in sorted(glob.glob(os.path.join(self.archive, "**", "*.md"), recursive=True)):
            with open(f, encoding="utf-8", errors="replace") as fh:
                for it in lire_archive(fh.read()):
                    par_source.setdefault("archive/" + it["src"], []).append(it)
        return sum(self.memoire.ajouter(src, items) for src, items in par_source.items())

    async def importer_historique(self) -> int:
        """Garde l'historique HA des entités choisies, depuis le dernier import."""
        if not self.historique:
            return 0
        try:
            fuseau = ZoneInfo((await self.appel("GET", "/config")).get("time_zone") or "Europe/Paris")
        except Exception:
            fuseau = None
        etats = await self.appel("GET", "/states")
        noms = {e["entity_id"]: e.get("attributes", {}).get("friendly_name") or e["entity_id"]
                for e in etats}
        entites = sorted({eid for eid in noms for motif in self.historique
                          if fnmatch.fnmatch(eid, motif)})
        if not entites:
            return 0
        fin = datetime.now(timezone.utc)
        dernier = self.memoire.meta("historique_jusqu_a")
        debut = (datetime.fromisoformat(dernier) - timedelta(minutes=5) if dernier
                 else fin - timedelta(days=self.historique_jours))
        nouvelles = 0
        for i in range(0, len(entites), 20):
            lot = ",".join(entites[i:i + 20])
            serie = await self.appel(
                "GET",
                f"/history/period/{quote(debut.isoformat())}?end_time={quote(fin.isoformat())}"
                f"&filter_entity_id={lot}&minimal_response&no_attributes",
            )
            nouvelles += self.memoire.ajouter("historique", lignes_historique(serie, noms, fuseau))
        self.memoire.meta("historique_jusqu_a", fin.isoformat())
        return nouvelles

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
                    "dernier_carnet": self.agent_memoire.dernier or None,
                    "derniere_purge": self.derniere_purge,
                    "dernier_passe": self.passe.dernier,
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
            for l in lignes:
                l["origine"] = libelle(l["source"])
            texte = " ; ".join(
                f"[{(l['date'] + ' ') if l['date'] else ''}{l['origine']}] {l['texte']}" for l in lignes)
            resultat = {"req_id": req_id, "ok": True, "lignes": lignes, "texte": texte[:2000]}
        except Exception as e:
            resultat = {"req_id": req_id, "ok": False, "lignes": [], "texte": "", "erreur": str(e)[:200]}
        await self.appel("POST", "/events/adria_core_resultat", resultat)

    async def faire_carnet(self, data: dict | None = None) -> None:
        data = data or {}
        essai = str(data.get("essai")).lower() == "true"
        try:
            res = await self.agent_memoire.carnet(essai=essai,
                                                  forcer=str(data.get("forcer")).lower() == "true")
        except Exception as e:
            LOG.exception("Carnet de bord")
            res = {"ok": False, "raison": str(e)[:200]}
        LOG.info("Carnet de bord (%s) : %s", "essai" if essai else "réel", res)
        try:
            await self.appel("POST", "/events/adria_core_carnet_resultat", res)
        except Exception:
            pass
        await self.publier_etat()

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
                    for i, evt in enumerate(("adria_core_recherche", "adria_core_synchroniser",
                                             "adria_core_ia", "adria_core_carnet",
                                             "adria_core_purge", "adria_core_habitudes",
                                             "mobile_app_notification_action"), 1):
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
                        elif e["event_type"] == "adria_core_ia":
                            asyncio.create_task(self.repondre_ia(e.get("data", {})))
                        elif e["event_type"] == "adria_core_carnet":
                            asyncio.create_task(self.faire_carnet(e.get("data", {})))
                        elif e["event_type"] == "adria_core_purge":
                            asyncio.create_task(self.purger(e.get("data", {})))
                        elif e["event_type"] == "adria_core_habitudes":
                            asyncio.create_task(self.habitudes_dimanche(e.get("data", {})))
                        elif e["event_type"] == "mobile_app_notification_action":
                            self.habitudes.recevoir_action(e.get("data", {}))
                            self.formulaires.recevoir_action(e.get("data", {}))
                            self.courriers.recevoir_action(e.get("data", {}))
                        elif e["event_type"] == "adria_core_synchroniser":
                            asyncio.create_task(self.synchroniser())
            except Exception as e:
                LOG.warning("Connexion perdue (%s), nouvel essai dans %s s", e, attente)
            await asyncio.sleep(attente)
            attente = min(attente * 2, 300)

    async def planifier(self, heure: str | None = None, tache=None) -> None:
        heure = heure or self.heure_sync
        tache = tache or self.nuit
        while True:
            h, m = (int(x) for x in heure.split(":"))
            maintenant = datetime.now()
            prochaine = maintenant.replace(hour=h, minute=m, second=0, microsecond=0)
            if prochaine <= maintenant:
                prochaine += timedelta(days=1)
            await asyncio.sleep((prochaine - maintenant).total_seconds())
            try:
                await tache()
            except Exception:
                LOG.exception("Tâche planifiée %s", heure)

    async def demarrer(self) -> None:
        async with aiohttp.ClientSession() as session:
            self.session = session
            await asyncio.sleep(10)  # laisser Home Assistant finir de démarrer
            await self.publier_etat_ia()
            await self.publier_etat_formulaires()
            try:
                await PageFormulaires(self.formulaires, self, self.courriers).demarrer()
            except Exception:
                LOG.exception("Page formulaires")
            await self.synchroniser()
            try:
                self.habitudes.calculer()
                await self.habitudes.publier()
            except Exception:
                LOG.exception("Habitudes")
            await asyncio.gather(self.ecouter(), self.battement(), self.planifier(),
                                 self.planifier(self.heure_carnet, self.faire_carnet),
                                 self.formulaires.surveiller(), self.courriers.surveiller(),
                                 self.planifier(self.heure_rappel, self.courriers.rappeler),
                                 self.planifier(self.heure_habitudes, self.habitudes_dimanche))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(Core().demarrer())
