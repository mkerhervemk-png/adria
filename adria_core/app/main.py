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

import banc as BANC
import brain as BR
import budget as B
from courriers import AgentCourriers
from devoirs import AgentDevoirs
from general import AgentGeneral
from menus import AgentMenus
from formulaires import AgentFormulaires
from guardian import Guardian
from habitudes import AgentHabitudes
from maintenance import AgentMaintenance
from analyse import AgentAnalyse, HEURES as HEURES_ANALYSE
from veille import AgentVeille
from bilan import AgentBilan, HEURE as HEURE_BILAN
from famille import AgentFamille, HEURE as HEURE_FAMILLE
from monde import AgentMonde
from notifs import AgentNotifs
from consolidation import AgentConsolidation
from evolution import Evolution
import evolution as EV
from mcp import ServeurMCP, lire_jetons
import confidentialite as CF
import procedures as PR
from procedures import Procedures
import sens as SE
from sens import Sens, fusionner
from memoire_agent import AgentMemoire
from passe import AgentPasse, libelle
from purge import LISTES as LISTES_PURGE, a_purger
from competences import AgentCompetences
from journal import AgentJournal
from retrouver import Retrouver
from routeur import Routeur
import suivis as S
from web import PageFormulaires
from store import Memoire, lignes_historique, lire_archive

LOG = logging.getLogger("adria_core")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
API = "http://supervisor/core/api"
WS = "ws://supervisor/core/websocket"
OPTIONS = "/data/options.json"
BASE = "/data/adria_core.db"
VERSION = "0.22.0"


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
        self.version = VERSION
        self.evolution = Evolution(self.memoire)  # lot E1
        self.procedures = Procedures(self.memoire)  # lot PR1
        self.sens = Sens(self.memoire, self.ollama_url, opts.get("sens_modele") or SE.MODELE,
                         float(opts.get("sens_seuil") or SE.SEUIL))  # lot S1
        self.mcp_jeton: str = str(opts.get("mcp_jeton") or "")
        # v0.21.2 (R1) : jetons MCP avec profil, politique de confidentialité côté serveur
        self.mcp_jetons = lire_jetons(self.mcp_jeton, opts.get("mcp_jetons") or [],
                                      opts.get("mcp_jeton_profil"))
        self.politique = CF.Politique(self.memoire, opts.get("memoire_sources_enfant") or None,
                                      opts.get("memoire_sources_inconnu") or None)
        self.mcp: ServeurMCP | None = None
        self.deja_connecte = False
        self.routeur = Routeur(self)
        self.agent_memoire = AgentMemoire(self)
        self.consolidation = AgentConsolidation(self)
        self.passe = AgentPasse(self)
        self.heure_carnet: str = opts.get("heure_carnet") or "23:40"
        self.heure_consolidation: str = opts.get("heure_consolidation") or "02:15"
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
        self.suivis = S.Suivis(self.memoire)  # lots A4/A5
        self.courriers.suivis = self.suivis
        self.courriers.apres_suivi = self.publier_suivis
        self.retrouver = Retrouver(self, self.formulaires)  # lot A6
        notif = opts.get("formulaire_notifier") or "notify.mobile_app_telephone_mael_sm_s921b"
        self.journal = AgentJournal(self, notif)  # lot J1
        self.heure_journal: str = opts.get("journal_heure") or "17:45"
        self.competences = AgentCompetences(self, notif)  # lot C1
        self.budget = B.Budget(self.memoire)  # lot B1
        self.courriers.budget = self.budget
        self.courriers.apres_budget = self.publier_budget
        self.menus = AgentMenus(self, notif)  # lot M1
        self.devoirs = AgentDevoirs(self, notif)  # lot N1
        self.general = AgentGeneral(self, notif)  # lot G1
        self.habitudes = AgentHabitudes(
            self, opts.get("formulaire_notifier") or "notify.mobile_app_telephone_mael_sm_s921b")
        self.heure_habitudes: str = opts.get("habitudes_heure") or "17:30"
        self.maintenance = AgentMaintenance(
            self, opts.get("formulaire_notifier") or "notify.mobile_app_telephone_mael_sm_s921b")
        self.analyse = AgentAnalyse(  # lot AN1 (23/11)
            self, opts.get("formulaire_notifier") or "notify.mobile_app_telephone_mael_sm_s921b")
        self.veille = AgentVeille(self)  # lot V1 (23/11)
        self.famille = AgentFamille(  # lot F1 lecture (24/11)
            self, opts.get("formulaire_notifier") or "notify.mobile_app_telephone_mael_sm_s921b")
        self.monde = AgentMonde(self)  # L1 + L2 (24/11)
        self.notifs = AgentNotifs(self)  # L3 + L5 (24/11)
        self.bilan = AgentBilan(  # lot AA1 (24/11)
            self, opts.get("formulaire_notifier") or "notify.mobile_app_telephone_mael_sm_s921b")
        # ASCENSION (v0.22.0) : Guardian indépendant (S3), BRAIN v1 (S2), banc d'évaluation (S1)
        self.guardian = Guardian(self.memoire, self.etat)
        self.brain = BR.Brain(self, self.guardian)
        self.banc_en_cours = False
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

    async def publier_suivis(self) -> None:
        try:
            e = self.suivis.etat()
            e["attributes"]["version"] = VERSION
            await self.appel("POST", f"/states/{S.CAPTEUR}", e)
        except Exception as e:
            LOG.warning("Etat abonnements non publié : %s", e)

    async def rappeler_suivis(self) -> None:
        """Chaque jour à l'heure de rappel : fins d'engagement et de garantie à J-30."""
        if await self.etat(S.INTERRUPTEUR) != "on":
            return
        msgs = S.rappels(self.suivis.factures(), self.suivis.garanties(), datetime.now().date())
        if msgs:
            domaine, service = self.courriers.notifier.split(".", 1)
            await self.appel("POST", f"/services/{domaine}/{service}", {
                "title": "📑 Abonnements et garanties", "message": "\n".join(msgs),
                "data": {"tag": "adria_suivis", "channel": "ADRIA courriers"}})
        await self.publier_suivis()

    # --- lot B1 budget --------------------------------------------------------------
    def abonnements_mois(self) -> float:
        try:
            return float(self.suivis.etat()["state"])
        except Exception:
            return 0.0

    async def publier_budget(self) -> None:
        try:
            e = self.budget.etat(self.abonnements_mois())
            e["attributes"]["version"] = VERSION
            await self.appel("POST", f"/states/{B.CAPTEUR}", e)
        except Exception as e:
            LOG.warning("Etat budget non publié : %s", e)

    async def budget_quotidien(self) -> None:
        """10h : alertes de dépassement ; le 1er du mois, bilan du mois écoulé."""
        if await self.etat(B.INTERRUPTEUR) != "on":
            return
        auj = datetime.now().date()
        dep = self.budget.depenses()
        domaine, service = self.courriers.notifier.split(".", 1)
        msgs = []
        if auj.day == 1:
            a, m = B.mois_suivant(auj.year, auj.month, -1)
            await self.appel("POST", f"/services/{domaine}/{service}", {
                "title": f"💶 Bilan de {B.libelle_mois(a, m)}",
                "message": B.bilan(dep, self.abonnements_mois(), a, m),
                "data": {"tag": "adria_budget_bilan", "channel": "ADRIA courriers"}})
        try:
            deja = set(json.loads(self.memoire.meta("budget_alertes") or "[]"))
        except ValueError:
            deja = set()
        for cle, msg in B.alertes(dep, auj, deja):
            deja.add(cle)
            msgs.append(msg)
        if msgs:
            self.memoire.meta("budget_alertes", json.dumps(sorted(deja)))
            await self.appel("POST", f"/services/{domaine}/{service}", {
                "title": "💶 Budget du mois", "message": "\n".join(msgs),
                "data": {"tag": "adria_budget", "channel": "ADRIA courriers"}})
        await self.publier_budget()

    async def budget_voix(self, data: dict) -> dict:
        res = {"req_id": data.get("req_id", ""), "ok": True}
        if await self.etat(B.INTERRUPTEUR) != "on":
            res["texte"] = "Le suivi du budget est éteint."
            return res
        q = str(data.get("question") or "")
        d = B.depense_vocale(q, datetime.now().date())
        if d:
            qui = str(data.get("qui") or "")
            if self.budget.ajouter(d, qui, "voix"):
                res["texte"] = (f"C'est noté : {B.euros(d['montant'])} en {B.LIBELLES[d['categorie']]}"
                                + (f" chez {d['magasin']}" if d["magasin"] else "") + ".")
                await self.publier_budget()
            else:
                res["texte"] = "Cette dépense était déjà notée."
            return res
        res["texte"] = B.repondre(q, self.budget.depenses(), self.abonnements_mois(), datetime.now().date())
        return res

    async def ws_commande(self, message: dict):
        """Une commande WebSocket ponctuelle (registre des entités…), sur une connexion à part."""
        assert self.session is not None
        async with self.session.ws_connect(WS) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            if (await ws.receive_json()).get("type") != "auth_ok":
                raise RuntimeError("auth refusée")
            await ws.send_json({"id": 1, **message})
            while True:
                rep = await ws.receive_json(timeout=60)
                if rep.get("id") == 1:
                    if not rep.get("success"):
                        raise RuntimeError(str(rep.get("error"))[:150])
                    return rep.get("result")

    async def repondre_evenement(self, nom: str, coro) -> None:
        """Lance un agent et renvoie son résultat dans l'événement `nom` (voix)."""
        try:
            res = await coro
        except Exception as e:
            LOG.warning("%s : %s", nom, type(e).__name__)
            res = {"ok": False, "texte": "Je n'ai pas pu le faire, il y a eu une erreur."}
        try:
            await self.appel("POST", f"/events/{nom}", res)
        except Exception:
            pass

    async def journal_dimanche(self, data: dict | None = None) -> None:
        try:
            await self.journal.proposer(forcer=str((data or {}).get("forcer")).lower() == "true")
        except Exception:
            LOG.exception("Journal de famille")

    async def papier(self, data: dict) -> None:
        """Lot A6 : « sors-moi le papier » (voix)."""
        try:
            res = await self.retrouver.demander(data)
        except Exception as e:
            LOG.warning("Papier : %s", type(e).__name__)
            res = {"req_id": data.get("req_id", ""), "ok": False, "texte": "Je n'ai pas pu chercher le papier."}
        try:
            await self.appel("POST", "/events/adria_core_papier_resultat", res)
        except Exception:
            pass

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
                    "par_profondeur_24h": self.memoire.stats_ia_profondeur(86400),
                    "echecs_7j": self.memoire.stats_ia_echecs(7 * 86400),
                    "coupe_circuit": self.routeur.cc.etat(),
                    "surcharge_gemini_s": self.routeur.surcharge.reste(),
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
            try:
                await self.general.minute()
            except Exception as e:
                LOG.warning("Rappels G1 : %s", str(e)[:150])
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
        self.evolution.suivre("synchronisation", "mémoire", not erreurs,
                              f"Recopie de la mémoire avec erreurs : {self.derniere_erreur}",
                              "recopie de nouveau sans erreur")
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
        await self.reprendre_procedures()
        await self.indexer_sens()

    async def maintenance_matin(self, data: dict | None = None) -> None:
        """Lot MA1 : diagnostic de maintenance 0 IA (06h30 ou événement adria_core_maintenance)."""
        try:
            res = await self.maintenance.diagnostic(data)
            if not res.get("essai"):
                for p in res.get("points", []):
                    if p["cle"] in res.get("nouveaux", []):
                        self.evolution.signaler(f"maintenance:{p['cle']}", "maison", p["texte"])
                for cle in res.get("regles", []):
                    self.evolution.corriger(f"maintenance:{cle}", "réglé (constaté au diagnostic du matin)")
                await self.publier_evolution()
        except Exception:
            LOG.exception("Maintenance")

    async def analyser(self, data: dict | None = None) -> None:
        """Lot AN1 : anomalies de la maison, 0 IA (08h, 12h, 18h ou événement adria_core_analyse)."""
        try:
            await self.analyse.analyser(data)
        except Exception:
            LOG.exception("Analyse")

    async def veiller(self, data: dict | None = None) -> None:
        """Lot V1 : veille du jour, 0 IA (06h45 ou événement adria_core_veille)."""
        try:
            await self.veille.veiller(data)
        except Exception:
            LOG.exception("Veille")

    async def famille_matin(self, data: dict | None = None) -> None:
        """Lot F1 : journée des enfants 07h00, 0 IA (ou événement adria_core_famille {essai})."""
        try:
            await self.famille.matin(data)
        except Exception:
            LOG.exception("Famille (matin)")

    async def bilan_dimanche(self, data: dict | None = None) -> None:
        """Lot AA1 : un seul bilan du dimanche 19h45 (ou événement adria_core_bilan_envoyer {essai})."""
        try:
            await self.bilan.envoyer(data)
        except Exception:
            LOG.exception("Bilan du dimanche")

    async def habitudes_dimanche(self, data: dict | None = None) -> None:
        data = data or {}
        try:
            await self.habitudes.proposer(essai=str(data.get("essai")).lower() == "true",
                                          forcer=str(data.get("forcer")).lower() == "true")
        except Exception:
            LOG.exception("Propositions d'habitudes")

    def _choisir(self, liste: str, source: str, items: list[dict], maintenant: datetime, essai: bool = False):
        """Lignes à retirer (avec copie dans le Core) et lignes choisies.
        v0.21.2 (R5) : en essai, AUCUNE écriture SQLite — on prévoit seulement que la recopie de
        l'exécution réelle couvrirait toutes les lignes ayant un uid."""
        choix = a_purger(liste, items, maintenant)
        if essai:
            return [i for i in choix if i.get("uid")], choix
        self.memoire.ajouter(source, items)  # recopie juste avant : rien ne se perd
        return [i for i in choix if i.get("uid") and self.memoire.a_la_copie(source, i)], choix

    async def purger(self, data: dict | None = None) -> dict:
        """Lot M2b-1. Retire des listes HA seulement les lignes dont le Core a la copie.
        essai=True : SIMULATION STRICTE (v0.21.2, R5) — aucune écriture SQLite, aucune suppression,
        aucune mise à jour d'état HA (sensor.adria_core) ni de la dernière purge ; seul le rapport
        est publié par l'événement adria_core_purge_resultat."""
        essai = str((data or {}).get("essai")).lower() == "true"
        maintenant = datetime.now()
        # Garde-fou centralisé : aucune suppression sans interrupteur ON.
        # Une erreur de lecture (etat -> None) ferme le garde-fou.
        bloquee = not essai and await self.etat("input_boolean.adria_core_purge") != "on"
        res: dict = {"quand": maintenant.isoformat(timespec="seconds"), "essai": essai,
                     "bloquee": bloquee,
                     "retirees": {}, "gardees_sans_copie": {}, "erreurs": [], "durees": {}}
        if essai:
            res.update(a_copier={}, operations=[])
        if bloquee:
            LOG.warning("Purge bloquée : input_boolean.adria_core_purge n'est pas ON")
        for liste in ([] if bloquee else LISTES_PURGE):
            source = liste.removeprefix("todo.")
            t0 = time.monotonic()
            try:
                rep = await self.appel("POST", "/services/todo/get_items?return_response",
                                       {"entity_id": liste})
                items = rep.get("service_response", {}).get(liste, {}).get("items", [])
                ok, choix = self._choisir(liste, source, items, maintenant, essai)
                res["durees"][source] = round(time.monotonic() - t0, 1)
                sans = len(choix) - len(ok)
                if essai and ok:
                    manquantes = sum(1 for i in ok if not self.memoire.a_la_copie(source, i))
                    if manquantes:
                        res["a_copier"][source] = manquantes
                    res["operations"].append(f"{liste} : copier {manquantes}, retirer {len(ok)}")
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
        if not essai and not bloquee:
            self.evolution.suivre("purge", "mémoire", not res["erreurs"],
                                  "Purge des listes HA avec erreurs : " + "; ".join(res["erreurs"])[:200],
                                  "purge de nouveau sans erreur")
        if not essai:  # la simulation ne change aucun état persistant
            self.derniere_purge = res
        try:
            await self.appel("POST", "/events/adria_core_purge_resultat", res)
        except Exception:
            pass
        if not essai:
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
                    "derniere_consolidation": self.consolidation.resume(),
                    "derniere_purge": self.derniere_purge,
                    "dernier_passe": self.passe.dernier,
                    "sens": self.sens.indexation or self.sens.stats(),
                    "mcp": {"actif": self.mcp is not None, "appels": self.mcp.appels if self.mcp else 0},
                    "version": VERSION,
                },
            })
        except Exception as e:
            LOG.warning("Etat non publié : %s", e)

    async def chercher_tout(self, question: str, n: int = 5, exclure: list[str] | None = None,
                            profil: str = CF.ADULTE) -> list[dict]:
        """Lot S1 : mots d'abord (P1) ; le sens complète s'il manque des réponses (interrupteur ON).
        v0.21.2 (R1) : la politique du profil s'ajoute TOUJOURS aux exclusions du client, et filtre
        encore le résultat (deuxième barrière). Profil invalide -> CF.AccesRefuse."""
        n = max(1, min(int(n), 20))
        exclure = self.politique.exclusions(profil, exclure)
        lignes = self.politique.filtrer(profil, self.memoire.chercher(question, n, exclure))
        if len(lignes) >= n or len(question.strip()) < 4 or await self.etat(SE.INTERRUPTEUR) != "on":
            return lignes
        try:
            sens = self.politique.filtrer(profil, await self.sens.chercher(question, n, exclure, delai=8))
            self.evolution.corriger("sens_recherche", "la recherche par le sens répond de nouveau")
        except Exception as e:
            LOG.warning("Recherche par le sens : %s", str(e)[:150])
            self.evolution.signaler("sens_recherche", "mémoire",
                                    f"Recherche par le sens indisponible ({type(e).__name__})")
            return lignes
        return fusionner(lignes, sens, n)

    async def repondre(self, data: dict) -> None:
        req_id = data.get("req_id", "")
        try:
            exclues = data.get("exclure_sources") or []
            lignes = await self.chercher_tout(
                data.get("question", ""),
                int(data.get("n", 5)),
                [exclues] if isinstance(exclues, str) else list(exclues),
                profil=CF.profil_evenement(data),
            )
            for l in lignes:
                l["origine"] = libelle(l["source"])
            texte = " ; ".join(
                f"[{(l['date'] + ' ') if l['date'] else ''}{l['origine']}] {l['texte']}" for l in lignes)
            resultat = {"req_id": req_id, "ok": True, "lignes": lignes, "texte": texte[:2000]}
        except Exception as e:
            resultat = {"req_id": req_id, "ok": False, "lignes": [], "texte": "", "erreur": str(e)[:200]}
        await self.appel("POST", "/events/adria_core_resultat", resultat)

    async def consolider(self, data: dict | None = None) -> None:
        """Lot M2b-2 : consolidation de nuit (essai, ou réelle si adria_core_consolidation_reelle ON)."""
        data = data or {}
        try:
            res = await self.consolidation.consolider(forcer=str(data.get("forcer")).lower() == "true")
        except Exception as e:
            LOG.exception("Consolidation")
            res = {"ok": False, "raison": str(e)[:200]}
        LOG.info("Consolidation (%s) : %s", "essai" if res.get("essai") else "réelle", {k: v for k, v in res.items() if k != "proposition"})
        self.evolution.suivre("consolidation", "mémoire", bool(res.get("ok")),
                              f"Consolidation de nuit en échec : {res.get('raison') or 'garde-fou'}",
                              f"consolidation réussie ({res.get('moteur') or '?'})")
        await self.publier_evolution()
        try:
            await self.appel("POST", "/events/adria_core_consolider_resultat", res)
            await self.publier_etat()
        except Exception:
            pass

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

    # --- lots S1 / PR1 / E1 / BUS1 (19/11) --------------------------------------------
    async def indexer_sens(self, forcer: bool = False, max_lignes: int = 50000) -> None:
        """Vecteurs de sens manquants : tout à 03h30 (ou événement adria_core_sens) ; 500 au démarrage
        pour ne pas charger le PC en journée."""
        if await self.etat(SE.INTERRUPTEUR) != "on":
            return
        try:
            if not await self.sens.installer_modele():
                self.evolution.signaler("sens_modele", "mémoire",
                                        f"Modèle de sens {self.sens.modele} absent d'Ollama")
                return
            self.evolution.corriger("sens_modele", f"modèle {self.sens.modele} présent dans Ollama")
            res = await self.sens.indexer(max_lignes=max_lignes)
            self.evolution.suivre("sens_index", "mémoire", not res.get("erreur"),
                                  f"Indexation par le sens interrompue : {res.get('erreur')}",
                                  "indexation par le sens terminée")
        except Exception:
            LOG.exception("Indexation sens")
        await self.publier_etat()

    async def reprendre_procedures(self) -> None:
        try:
            from habitudes import REGLES
            lignes = [r[0] for r in self.memoire.db.execute(
                "SELECT resume FROM lignes WHERE source = 'competences'").fetchall()]
            self.procedures.reprendre(self.competences._lire("comp_creees", []), lignes,
                                      self.habitudes.rappels, REGLES)
        except Exception:
            LOG.exception("Reprise des procédures")
        await self.publier_capteur(PR.CAPTEUR, self.procedures.etat())

    async def procedure_voix(self, data: dict) -> dict:
        if await self.etat(PR.INTERRUPTEUR) != "on":
            return {"req_id": data.get("req_id", ""), "ok": False, "texte": "La mémoire des procédures est éteinte."}
        res = self.procedures.repondre(data)
        await self.publier_capteur(PR.CAPTEUR, self.procedures.etat())
        return res

    async def correction(self, data: dict) -> dict:
        res = self.evolution.lecon(data)
        res["req_id"] = data.get("req_id", "")
        await self.publier_evolution()
        return res

    async def publier_capteur(self, entite: str, etat: dict) -> None:
        try:
            etat["attributes"]["version"] = VERSION
            await self.appel("POST", f"/states/{entite}", etat)
        except Exception as e:
            LOG.warning("%s non publié : %s", entite, e)

    async def publier_evolution(self) -> None:
        await self.publier_capteur(EV.CAPTEUR, self.evolution.etat())

    async def publier_tout(self) -> None:
        """Après un redémarrage de HA : republier tous les capteurs du Core."""
        await asyncio.sleep(20)
        for f in (self.publier_etat, self.publier_etat_ia, self.publier_etat_formulaires, self.publier_suivis,
                  self.publier_budget, self.devoirs.publier, self.habitudes.publier, self.maintenance.publier,
                  self.analyse.publier, self.veille.publier, self.famille.publier, self.monde.publier, self.notifs.publier,
                  self.publier_evolution):
            try:
                await f()
            except Exception as e:
                LOG.warning("Republication : %s", str(e)[:120])
        await self.publier_capteur(PR.CAPTEUR, self.procedures.etat())
        await self.publier_capteur(BR.CAPTEUR, self.brain.etat())
        LOG.info("Capteurs du Core republiés après reconnexion")

    # --- ASCENSION (v0.22.0) -----------------------------------------------------------
    async def brain_voix(self, data: dict) -> dict:
        res = await self.brain.repondre(data)
        await self.publier_capteur(BR.CAPTEUR, self.brain.etat())
        return res

    async def lancer_banc(self, data: dict | None = None) -> None:
        """S1 : banc d'évaluation (lecture seule). Un seul passage à la fois."""
        if self.banc_en_cours:
            return
        self.banc_en_cours = True
        data = data or {}
        try:
            moteur = str(data.get("moteur") or "")
            res = await BANC.executer(self.brain, self.politique, VERSION,
                                      moteur if moteur in ("gemini", "gemini_lite", "claude", "ollama") else "",
                                      str(data.get("categorie") or ""))
            historique = BANC.garder(self.memoire, res)
            LOG.info("Banc d'évaluation : %s %% (%s/%s), moteur %s", res["score"], res["reussis"], res["total"],
                     res["moteur"])
            await self.publier_capteur(BANC.CAPTEUR, BANC.capteur(res, historique))
        except Exception:
            LOG.exception("Banc d'évaluation")
        finally:
            self.banc_en_cours = False
            await self.publier_capteur(BR.CAPTEUR, self.brain.etat())

    def resume_etat(self) -> dict:
        s = self.memoire.stats()
        return {"version": VERSION, "lignes": s["lignes"], "depuis": s["depuis"],
                "derniere_sync": self.derniere_sync, "derniere_erreur": self.derniere_erreur,
                "derniere_purge": self.derniere_purge, "derniere_consolidation": self.consolidation.resume(),
                "maintenance": (self.maintenance.dernier or {}).get("niveau"),
                "analyse": len((self.analyse.dernier or {}).get("anomalies", [])),
                "sens": self.sens.indexation or self.sens.stats(), "ia_24h": self.memoire.stats_ia(86400),
                "erreurs_ouvertes": len(self.evolution.lister("ouverte", 100)),
                "procedures": len(self.procedures.toutes()),
                "guardian": self.guardian.stats(), "brain": (self.brain.dernier or {}).get("statut")}

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
                                             "adria_core_consolider", "adria_core_papier",
                                             "adria_core_souvenir", "adria_core_livre", "adria_core_journal",
                                             "adria_core_apprendre", "adria_core_competences_couper",
                                             "adria_core_general", "adria_core_budget", "adria_core_menus",
                                             "adria_core_devoirs", "adria_core_maintenance",
                                             "adria_core_procedure", "adria_core_correction", "adria_core_sens",
                                             "adria_core_analyse", "adria_core_veille",
                                             "adria_core_bilan", "adria_core_bilan_envoyer", "adria_core_famille",
                                             "adria_core_monde", "adria_core_brain", "adria_core_banc",
                                             "call_service",
                                             "mobile_app_notification_action"), 1):
                        await ws.send_json({"id": i, "type": "subscribe_events", "event_type": evt})
                    LOG.info("Connecté à Home Assistant")
                    attente = 5
                    if self.deja_connecte:  # HA a redémarré : ses capteurs « API » ont disparu
                        asyncio.create_task(self.publier_tout())
                    self.deja_connecte = True
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
                        elif e["event_type"] == "adria_core_consolider":
                            asyncio.create_task(self.consolider(e.get("data", {})))
                        elif e["event_type"] == "adria_core_souvenir":
                            d = e.get("data", {})
                            asyncio.create_task(self.repondre_evenement(
                                "adria_core_souvenir_resultat", self.journal.garder_voix(d)))
                        elif e["event_type"] == "adria_core_livre":
                            asyncio.create_task(self.journal.livre((e.get("data") or {}).get("annee")))
                        elif e["event_type"] == "adria_core_journal":
                            asyncio.create_task(self.journal_dimanche(e.get("data", {})))
                        elif e["event_type"] == "adria_core_apprendre":
                            d = e.get("data", {})
                            asyncio.create_task(self.repondre_evenement(
                                "adria_core_apprendre_resultat", self.competences.apprendre(d)))
                        elif e["event_type"] == "adria_core_general":
                            d = e.get("data", {})
                            if str(d.get("asynchrone")).lower() == "true":
                                asyncio.create_task(self.general.secours(d))
                            else:
                                asyncio.create_task(self.repondre_evenement(
                                    "adria_core_general_resultat", self.general.repondre(d)))
                        elif e["event_type"] == "adria_core_budget":
                            asyncio.create_task(self.repondre_evenement(
                                "adria_core_budget_resultat", self.budget_voix(e.get("data", {}))))
                        elif e["event_type"] == "adria_core_menus":
                            asyncio.create_task(self.repondre_evenement(
                                "adria_core_menus_resultat", self.menus.voix(e.get("data", {}))))
                        elif e["event_type"] == "adria_core_devoirs":
                            asyncio.create_task(self.repondre_evenement(
                                "adria_core_devoirs_resultat", self.devoirs.aider(e.get("data", {}))))
                        elif e["event_type"] == "adria_core_competences_couper":
                            asyncio.create_task(self.competences.couper_tout())
                        elif e["event_type"] == "adria_core_papier":
                            asyncio.create_task(self.papier(e.get("data", {})))
                        elif e["event_type"] == "adria_core_habitudes":
                            asyncio.create_task(self.habitudes_dimanche(e.get("data", {})))
                        elif e["event_type"] == "mobile_app_notification_action":
                            self.habitudes.recevoir_action(e.get("data", {}))
                            self.formulaires.recevoir_action(e.get("data", {}))
                            self.courriers.recevoir_action(e.get("data", {}))
                            self.journal.recevoir_action(e.get("data", {}))
                            asyncio.create_task(self.competences.recevoir_action(e.get("data", {})))
                            asyncio.create_task(self.menus.recevoir_action(e.get("data", {})))
                            asyncio.create_task(self.general.recevoir_action(e.get("data", {})))
                        elif e["event_type"] == "adria_core_maintenance":
                            asyncio.create_task(self.maintenance_matin(e.get("data", {})))
                        elif e["event_type"] == "adria_core_analyse":
                            asyncio.create_task(self.analyser(e.get("data", {})))
                        elif e["event_type"] == "call_service":
                            if (e.get("data") or {}).get("domain") == "notify":
                                asyncio.create_task(self.notifs.recevoir(e.get("data", {})))
                        elif e["event_type"] == "adria_core_monde":
                            asyncio.create_task(self.monde.calculer({**(e.get("data") or {}), "forcer": True}))
                        elif e["event_type"] == "adria_core_famille":
                            asyncio.create_task(self.famille_matin(e.get("data", {})))
                        elif e["event_type"] == "adria_core_bilan":
                            self.bilan.recevoir(e.get("data", {}))
                        elif e["event_type"] == "adria_core_bilan_envoyer":
                            asyncio.create_task(self.bilan_dimanche(e.get("data", {})))
                        elif e["event_type"] == "adria_core_veille":
                            asyncio.create_task(self.veiller(e.get("data", {})))
                        elif e["event_type"] == "adria_core_procedure":
                            asyncio.create_task(self.repondre_evenement(
                                "adria_core_procedure_resultat", self.procedure_voix(e.get("data", {}))))
                        elif e["event_type"] == "adria_core_correction":
                            asyncio.create_task(self.repondre_evenement(
                                "adria_core_correction_resultat", self.correction(e.get("data", {}))))
                        elif e["event_type"] == "adria_core_sens":
                            asyncio.create_task(self.indexer_sens(forcer=True))
                        elif e["event_type"] == "adria_core_brain":
                            asyncio.create_task(self.repondre_evenement(
                                "adria_core_brain_resultat", self.brain_voix(e.get("data", {}))))
                        elif e["event_type"] == "adria_core_banc":
                            asyncio.create_task(self.lancer_banc(e.get("data", {})))
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
            await self.publier_suivis()
            await self.publier_budget()
            await self.devoirs.publier()
            try:
                await PageFormulaires(self.formulaires, self, self.courriers, self.retrouver).demarrer()
            except Exception:
                LOG.exception("Page formulaires")
            self.sens.session = session
            if self.mcp_jetons:
                try:
                    self.mcp = ServeurMCP(self, self.mcp_jetons)
                    await self.mcp.demarrer()
                except Exception:
                    LOG.exception("Bus MCP")
                    self.mcp = None
            await self.synchroniser()
            try:
                self.habitudes.calculer()
                await self.habitudes.publier()
            except Exception:
                LOG.exception("Habitudes")
            await self.reprendre_procedures()
            await self.publier_evolution()
            asyncio.create_task(self.indexer_sens(max_lignes=500))
            await asyncio.gather(self.ecouter(), self.battement(), self.planifier(),
                                 self.planifier(self.heure_carnet, self.faire_carnet),
                                 self.planifier(self.heure_consolidation, self.consolider),
                                 self.formulaires.surveiller(), self.courriers.surveiller(),
                                 self.planifier(self.heure_rappel, self.courriers.rappeler),
                                 self.planifier(self.heure_rappel, self.rappeler_suivis),
                                 self.planifier(self.heure_journal, self.journal_dimanche),
                                 self.planifier("10:05", self.journal.livre_si_15_decembre),
                                 self.planifier(self.heure_habitudes, self.habitudes_dimanche),
                                 self.planifier("10:02", self.budget_quotidien),
                                 self.planifier("17:15", self.menus.proposer),
                                 self.planifier("16:30", self.menus.ce_soir),
                                 self.planifier("19:15", self.devoirs.bilan_soir),
                                 self.planifier("06:30", self.maintenance_matin),
                                 self.planifier("06:45", self.veiller),
                                 self.planifier(HEURE_BILAN, self.bilan_dimanche),
                                 self.planifier(HEURE_FAMILLE, self.famille_matin),
                                 self.monde.boucle(),
                                 *(self.planifier(h, self.analyser) for h in HEURES_ANALYSE))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(Core().demarrer())
