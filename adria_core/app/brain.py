"""BRAIN v1 — boucle de raisonnement d'ADRIA (ASCENSION S2, v0.22.0).

Au lieu d'envoyer la question à un modèle et de rendre sa réponse, BRAIN :
  1. vérifie le profil auprès du GUARDIAN (refus par défaut) ;
  2. refuse tout de suite, sans IA, une commande touchant la sécurité ou les garde-fous ;
  3. cherche d'abord dans la mémoire (0 IA) ;
  4. laisse le modèle choisir au plus `tours` outils EN LECTURE SEULE (chaque appel passe par le Guardian) ;
  5. vérifie la réponse sans IA : preuves existantes, « je sais / je suppose / j'ignore », contradictions ;
  6. rend une réponse SOURCÉE. Une action n'est jamais exécutée : au mieux une proposition (OUI de Mael).

Déclenchement : événement `adria_core_brain` {question, qui | profil, voix, req_id}
-> `adria_core_brain_resultat`. Actif si input_boolean.adria_core_brain est ON (absent = éteint).
Bornes : 3 tours (2 à la voix), 8 outils par mission, observations tronquées, 60 missions par jour.
Les réponses de BRAIN ne sont PAS écrites dans la mémoire longue (pas d'auto-renforcement : une réponse
d'IA ne doit jamais redevenir une « preuve »). Les 20 dernières missions sont gardées dans `meta`.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import date, datetime, timedelta
from urllib.parse import quote

import confidentialite as CF
import fiabilite as FI
import guardian as GD
from formulaires import lire_json
from passe import libelle

LOG = logging.getLogger("adria_core.brain")

INTERRUPTEUR = "input_boolean.adria_core_brain"
CAPTEUR = "sensor.adria_core_brain"
MAX_JOUR = 60
# v0.22.2 : durée maximale d'une mission (secondes). Au-delà, plus aucun appel IA : réponse rendue avec ce
# qui a déjà été trouvé. Les deux modes utilisent la chaîne « brain » du routeur (v0.22.5 : Gemini Lite d'abord).
DELAI_TEXTE, DELAI_VOIX = 35.0, 15.0  # v0.22.3 : 35 s en texte (Gemini saturé -> Gemini Lite)
MARGE_MIN = 4.0  # pas de nouvel appel IA s'il reste moins que cela
OBS_MAX = 1500       # caractères par observation envoyée au modèle
CONTEXTE_MAX = 6500  # caractères d'observations au total
ADULTES = {"mael", "vanessa"}
ENFANTS = {"ryan", "iwan", "aelia"}

OUTILS: dict[str, tuple[str, str]] = {
    "memoire_chercher": ("Cherche dans la mémoire longue du foyer (mots puis sens).", '{"question": "..."}'),
    "memoire_periode": ("Ce qui s'est passé entre deux dates.", '{"debut": "AAAA-MM-JJ", "fin": "AAAA-MM-JJ", "mots": ""}'),
    "procedures_chercher": ("Comment faire une tâche (procédures, compétences apprises).", '{"question": "..."}'),
    "evolution_journal": ("Erreurs ouvertes d'ADRIA et leçons corrigées.", '{"statut": "ouverte|corrigée"}'),
    "etat_core": ("État technique d'ADRIA Core (version, mémoire, moteurs IA 24 h).", "{}"),
    "carnet_dernier": ("Dernier carnet de bord du soir.", "{}"),
    "habitudes": ("Habitudes repérées et rappels acceptés.", "{}"),
    "agenda": ("Événements des agendas sur les prochains jours (1 à 14).", '{"jours": 7}'),
    "vue_foyer": ("Vue actuelle du foyer (qui est où, maison, météo) + anticipations 24 h.", "{}"),
    "etat_entite": ("État actuel d'un appareil : entity_id exact ou quelques mots du nom.", '{"entite": "..."}'),
    "historique_entite": ("Changements d'état d'un appareil (entity_id exact) sur N heures (1 à 48).",
                          '{"entite": "light.salon", "heures": 24}'),
}
# Fiabilité d'une observation faite par un outil (les lignes de mémoire ont leur propre note)
CONFIANCE_OUTIL = {"etat_core": 0.95, "etat_entite": 0.9, "historique_entite": 0.9, "agenda": 0.85,
                   "vue_foyer": 0.8, "procedures_chercher": 0.85, "evolution_journal": 0.8,
                   "carnet_dernier": 0.6, "habitudes": 0.6}

COMMANDE = re.compile(r"^\s*(peux tu |tu peux |pourrais tu )?(desarm|arme|coupe|eteins|eteindre|allume|ouvre|ferme|"
                      r"deverrouill|verrouill|desactiv|active|arrete|stoppe|lance|demarre|supprim|efface|change|"
                      r"modifie|mets|met )")
# v0.22.4 : questions sur l'état technique du Core -> etat_core consulté d'office (sans IA), avant le modèle
TECHNIQUE = re.compile(r"\b(version|adria core|le core|ton core|erreurs? ouvertes?|journal des erreurs|moteurs?( d)? ia|"
                       r"routeur|recherche par le sens|indexation|ta memoire|tes moteurs|ton etat|tu vas bien)\b")
SANS_ARGUMENT = ("etat_core", "carnet_dernier", "habitudes", "vue_foyer")  # même résultat à chaque appel
NE_SAIS_PAS = ("Je n'ai trouvé aucune trace fiable de cela dans ma mémoire ni dans la maison. "
               "Je préfère te le dire plutôt que d'inventer.")

PROMPT = (
    "Tu es BRAIN, le raisonnement d'ADRIA (maison Kerherve). Nous sommes le {maintenant}. {qui}\n"
    "Tu dois repondre a la DEMANDE en t appuyant sur des OBSERVATIONS reelles. Tu peux appeler UN outil "
    "(lecture seule) par tour ; il te reste {tours} tour(s). Outils permis :\n{outils}\n"
    "Regles : n invente jamais un fait sur la maison ou la famille ; cite les references [M1], [O2]… des "
    "observations utilisees ; si rien ne repond, dis que tu ne sais pas. Tu n executes jamais d action : "
    "si une action aiderait, decris-la dans \"proposition\" (Mael devra dire OUI). Question de culture "
    "generale (rien sur la maison) : type=general, pas de preuve exigee.\n"
    "Reponds UNIQUEMENT par un JSON, soit {{\"action\": \"outil\", \"outil\": \"nom\", \"args\": {{...}}, "
    "\"pourquoi\": \"court\"}}, soit {{\"action\": \"repondre\", \"type\": \"maison|general\", "
    "\"reponse\": \"2 a 5 phrases, tutoiement, sans liste\", \"preuves\": [\"M1\"], \"proposition\": null}}.\n"
    "OBSERVATIONS :\n{observations}\nDEMANDE : {question}"
)


RANG = {CF.INCONNU: 0, CF.ENFANT: 1, CF.ADULTE: 2}


def profil_de(data: dict) -> str:
    """Profil de la demande (v0.22.1). Il est d'abord déduit de `qui` (personne reconnue par HA ; absent ->
    inconnu). Un champ `profil` fourni par l'appelant ne peut que RESTREINDRE, jamais élargir : on garde le
    plus strict des deux ; un `profil` invalide donne « inconnu »."""
    p = str(data.get("qui") or "").split(" ")[0].lower().replace("ë", "e").replace("é", "e")
    deduit = CF.ADULTE if p in ADULTES else CF.ENFANT if p in ENFANTS else CF.INCONNU
    if data.get("profil") in (None, ""):
        return deduit
    fourni = CF.normaliser_profil(data.get("profil")) or CF.INCONNU
    return fourni if RANG[fourni] < RANG[deduit] else deduit


_MOTS_VIDES = {"avec", "dans", "pour", "plus", "tout", "cette", "sont", "etre", "avoir", "fait", "faire",
               "est", "les", "des", "une", "que", "qui", "pas", "mais", "aussi", "comme", "tres", "bien",
               "peut", "rien", "trouve", "trouvee", "actuellement", "maintenant", "selon", "dernier",
               "derniere", "aucune", "aucun", "ainsi", "alors"}


def _termes(t: str) -> set[str]:
    plat = CF._plat(t)
    mots = {m[:6] for m in re.findall(r"[a-z]{4,}", plat) if m not in _MOTS_VIDES}
    return mots | set(re.findall(r"\d+", plat))


def pertinentes(texte: str, preuves: list[dict]) -> list[dict]:
    """v0.22.1 : ne garde que les preuves qui partagent au moins un terme significatif (mot de 4 lettres et
    plus, ou nombre) avec la réponse. Une preuve citée mais sans rapport ne justifie rien."""
    t = _termes(texte)
    return [p for p in preuves if t & _termes(str(p.get("texte") or ""))]


def est_commande_sensible(question: str) -> bool:
    plat = CF._plat(question).strip()
    return bool(COMMANDE.search(plat)) and GD.texte_sensible(question)


def resume_outil(o: dict) -> str:
    """v0.22.3 : phrase lisible, SANS IA, à partir du résultat brut d'un outil (réponse de secours)."""
    nom, brut = o.get("outil"), o.get("brut")
    try:
        if nom == "etat_core" and isinstance(brut, dict):
            t = (f"ADRIA Core {brut.get('version')}, {brut.get('lignes')} lignes en mémoire, "
                 f"{brut.get('erreurs_ouvertes', 0)} erreur(s) ouverte(s)")
            return t + (f", maintenance : {brut['maintenance']}" if brut.get("maintenance") else "")
        if nom == "etat_entite" and isinstance(brut, list):
            return " ; ".join(f"{e.get('nom') or e.get('entity_id')} : {e.get('etat')}" for e in brut[:4])
        if nom == "agenda" and isinstance(brut, list):
            return "agenda : " + " ; ".join(str(x) for x in brut[:3])
        if nom == "vue_foyer" and isinstance(brut, dict):
            return str(brut.get("foyer") or "")[:200]
        if nom == "evolution_journal" and isinstance(brut, list):
            return f"{len(brut)} entrée(s) au journal des erreurs" + (
                f", dont : {str(brut[0].get('erreur') or brut[0])[:120]}" if brut and isinstance(brut[0], dict) else "")
        if nom == "historique_entite" and isinstance(brut, list):
            return "historique : " + " ; ".join(str(x) for x in brut[-3:])
    except Exception:
        pass
    return re.sub(r"[{}\[\]\"]", "", str(o.get("texte") or ""))[:160]


def _court(obj, n: int = OBS_MAX) -> str:
    t = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return t if len(t) <= n else t[: n - 1] + "…"


class Brain:
    def __init__(self, core, guardian: GD.Guardian):
        self.core = core
        self.guardian = guardian
        self.registre = FI.Registre(core.memoire)
        self.dernier: dict | None = None
        self.missions = 0

    # ------------------------------------------------------------------ compteurs
    def _meta(self, cle, defaut):
        try:
            return json.loads(self.core.memoire.meta(cle) or "null") or defaut
        except ValueError:
            return defaut

    def compter(self, m: datetime) -> bool:
        c = self._meta("brain_compte", {})
        if c.get("date") != m.date().isoformat():
            c = {"date": m.date().isoformat(), "n": 0}
        if c["n"] >= MAX_JOUR:
            return False
        c["n"] += 1
        self.core.memoire.meta("brain_compte", json.dumps(c))
        return True

    # ------------------------------------------------------------------ outils (lecture seule)
    async def outil(self, nom: str, args: dict, profil: str, question: str, banc: bool = False) -> object:
        c = self.core
        if nom == "memoire_chercher":
            q = str(args.get("question") or question)[:300]
            # le banc ne doit laisser aucune trace dans le journal des erreurs du Core
            return await c.chercher_tout(q, 6, profil=profil, journaliser=not banc)
        if nom == "memoire_periode":
            from store import mots_cles
            pol = c.politique
            lignes = c.memoire.chercher_periode(str(args.get("debut"))[:10], str(args.get("fin"))[:10],
                                                mots_cles(str(args.get("mots") or "")), 8, pol.exclusions(profil))
            return pol.filtrer(profil, lignes)
        if nom == "procedures_chercher":
            q = str(args.get("question") or "").strip()
            return c.procedures.chercher(q, 5) if q else c.procedures.toutes()[:10]
        if nom == "evolution_journal":
            st = args.get("statut") if args.get("statut") in ("ouverte", "corrigée") else None
            return c.evolution.lister(st, 8)
        if nom == "etat_core":
            return c.resume_etat()
        if nom == "carnet_dernier":
            return c.agent_memoire.dernier or "Pas encore de carnet."
        if nom == "habitudes":
            return {"reperees": getattr(c.habitudes, "derniers", [])[:8], "rappels": c.habitudes.rappels[:8]}
        if nom == "agenda":
            j = max(1, min(int(args.get("jours") or 7), 14))
            auj = date.today()
            evts = await c.menus.agenda([auj + timedelta(days=k) for k in range(j)])
            return [f"{str(e.get('start'))[:16].replace('T', ' ')} {e.get('summary')}" for e in evts[:30]]
        if nom == "vue_foyer":
            res = {}
            for cle, ent in (("foyer", "sensor.adria_core_monde"), ("anticipations", "sensor.adria_core_anticipations")):
                try:
                    res[cle] = ((await c.appel("GET", f"/states/{ent}")).get("attributes") or {}).get("texte")
                except Exception:
                    res[cle] = None
            return res
        if nom == "etat_entite":
            return await self._etat_entite(str(args.get("entite") or ""))
        if nom == "historique_entite":
            return await self._historique(str(args.get("entite") or ""), args.get("heures"))
        raise KeyError(nom)

    async def _etat_entite(self, entite: str) -> list[dict]:
        c = self.core
        if re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", entite):
            if not GD.entite_autorisee(entite):  # 2e barrière (le Guardian a déjà refusé)
                return []
            e = await c.appel("GET", f"/states/{entite}")
            return [{"entity_id": entite, "nom": (e.get("attributes") or {}).get("friendly_name"),
                     "etat": e.get("state"), "depuis": str(e.get("last_changed"))[:16]}]
        from competences import choisir_entites
        etats = await c.appel("GET", "/states")
        par_id = {e["entity_id"]: e for e in etats}
        res = []
        for eid, nom in choisir_entites(entite, etats, 5):
            if eid in par_id and GD.entite_autorisee(eid):
                res.append({"entity_id": eid, "nom": nom, "etat": par_id[eid].get("state"),
                            "depuis": str(par_id[eid].get("last_changed"))[:16]})
        return res

    async def _historique(self, entite: str, heures) -> list[str]:
        if not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", entite):
            return ["entity_id exact demandé (ex. light.salon)"]
        if not GD.entite_autorisee(entite):  # 2e barrière (le Guardian a déjà refusé)
            return []
        h = max(1, min(int(heures or 24), 48))
        debut = (datetime.now().astimezone() - timedelta(hours=h)).isoformat(timespec="seconds")
        rep = await self.core.appel(
            "GET", f"/history/period/{quote(debut)}?filter_entity_id={entite}&minimal_response&no_attributes")
        etats = rep[0] if rep and isinstance(rep, list) and rep[0] else []
        return [f"{str(e.get('last_changed'))[5:16].replace('T', ' ')} {e.get('state')}" for e in etats[-30:]]

    # ------------------------------------------------------------------ observations
    def _observer(self, obs: list[dict], nom: str, resultat, numero: dict) -> None:
        if nom in ("memoire_chercher", "memoire_periode") and isinstance(resultat, list):
            lignes = []
            for l in resultat:
                numero["M"] += 1
                lignes.append({**l, "ref": f"M{numero['M']}"})
            lignes = FI.annoter(lignes)
            for l in lignes:
                obs.append({"ref": l["ref"], "outil": nom, "date": l.get("date") or "", "source": l.get("source"),
                            "texte": str(l.get("texte") or "")[:300], "confiance": l["confiance"],
                            "nature": l["nature"]})
            if not lignes:
                obs.append({"ref": None, "outil": nom, "texte": "aucune ligne trouvée", "confiance": 0})
            return
        numero["O"] += 1
        vide = resultat in (None, "", [], {})
        obs.append({"ref": f"O{numero['O']}", "outil": nom, "date": date.today().isoformat(),
                    "source": nom, "texte": _court(resultat if not vide else "rien"),
                    "confiance": 0 if vide else CONFIANCE_OUTIL.get(nom, 0.7), "nature": FI.OBSERVE,
                    "brut": None if vide else resultat})  # v0.22.3 : pour la réponse de secours (jamais au modèle)

    @staticmethod
    def _rendu(obs: list[dict]) -> str:
        lignes, total = [], 0
        for o in obs:
            tete = f"[{o['ref']}] " if o.get("ref") else "[-] "
            src = f"({o.get('outil')}{', ' + o['date'] if o.get('date') else ''}, fiabilité {o.get('confiance')})"
            t = f"{tete}{src} {o['texte']}"
            total += len(t)
            if total > CONTEXTE_MAX:
                lignes.append("[…observations tronquées]")
                break
            lignes.append(t)
        return "\n".join(lignes) or "(aucune)"

    # ------------------------------------------------------------------ mission
    async def repondre(self, data: dict, maintenant: datetime | None = None, banc: bool = False,
                       seulement: str = "") -> dict:
        """`banc` (banc d'évaluation, interne) : ignore l'interrupteur et le quota ; `seulement` force un moteur."""
        t0 = time.monotonic()
        m = maintenant or datetime.now()
        mission = uuid.uuid4().hex[:12]
        q = str(data.get("question") or "").strip()[:600]
        profil = profil_de(data)
        voix = str(data.get("voix")).lower() == "true"
        res = {"req_id": data.get("req_id", ""), "mission": mission, "ok": False, "texte": "", "profil": profil,
               "statut": "ignore", "preuves": [], "outils": [], "contradictions": [], "proposition": None,
               "moteur": None, "tours": 0, "etapes": []}

        def etape(nom: str, debut: float, **k) -> None:  # v0.22.4 : durée de chaque étape (diagnostic)
            res["etapes"].append({"etape": nom, "s": round(time.monotonic() - debut, 1), **k})

        def fin(texte: str, ok: bool = True, **k) -> dict:
            res.update(ok=ok, texte=texte, duree=round(time.monotonic() - t0, 1), **k)
            self.guardian.fin_mission(mission)
            if not banc:  # le banc ne remplace pas l'historique des vraies missions
                self._garder(res, q, m)
            return res

        if not banc and await self.core.etat(INTERRUPTEUR) != "on":
            return fin("Mon raisonnement avancé est éteint pour le moment.", ok=False, refus="interrupteur")
        if not q:
            return fin("Je n'ai pas compris la question.", ok=False)
        # 1. Commande touchant la sécurité ou les garde-fous : refus sans IA
        if est_commande_sensible(q):
            d = await self.guardian.decider("proposer_action", profil, {"texte": q}, mission)
            return fin("Je ne peux pas faire ça : " + d.raison + ". Les actions de sécurité restent entre tes mains.",
                       ok=False, refus=d.raison)
        if not banc and not self.compter(m):
            return fin("J'ai atteint ma limite de raisonnements pour aujourd'hui.", ok=False, refus="quota")
        self.missions += 1
        obs: list[dict] = []
        numero = {"M": 0, "O": 0}
        # 2. Mémoire d'abord, sans IA
        t_mem = time.monotonic()
        d = await self.guardian.decider("memoire_chercher", profil, {"question": q}, mission)
        if not d.autorise:
            return fin("Je ne peux pas répondre : " + d.raison + ".", ok=False, refus=d.raison)
        try:
            self._observer(obs, "memoire_chercher",
                           await self.outil("memoire_chercher", {"question": q}, profil, q, banc), numero)
            res["outils"].append("memoire_chercher")
        except CF.AccesRefuse as e:
            return fin(f"Je ne peux pas répondre : {e}.", ok=False, refus=str(e))
        except Exception as e:
            LOG.warning("BRAIN mémoire : %s", str(e)[:120])
        etape("memoire", t_mem, lignes=sum(1 for o in obs if str(o.get("ref") or "").startswith("M")))
        # v0.22.4 : question technique sur le Core -> état du Core lu d'office (0 IA ; le Guardian décide)
        if TECHNIQUE.search(CF._plat(q)):
            t_core = time.monotonic()
            d = await self.guardian.decider("etat_core", profil, {}, mission)
            if d.autorise:
                try:
                    self._observer(obs, "etat_core", await self.outil("etat_core", {}, profil, q, banc), numero)
                    res["outils"].append("etat_core")
                except Exception as e:
                    LOG.warning("BRAIN etat_core : %s", str(e)[:120])
            etape("etat_core_d_office", t_core, autorise=d.autorise)
        permis = [n for n in OUTILS if profil in GD.REGISTRE[n]["profils"]]
        catalogue = "\n".join(f"- {n} : {OUTILS[n][0]} args {OUTILS[n][1]}" for n in permis)
        jj = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        qui = {"adulte": f"C est {str(data.get('qui') or 'un adulte').split(' ')[0].capitalize()} (adulte) qui parle.",
               "enfant": "C est un enfant qui parle : langage simple, rien sur l argent, la securite ni les reglages.",
               "inconnu": "Voix non reconnue : reste general, rien sur la vie du foyer."}[profil]
        tours = 2 if voix else 3
        # v0.22.2 : à la voix, une preuve fiable déjà en mémoire suffit -> un seul tour (réponse directe)
        if voix and any(o.get("ref") and o.get("confiance", 0) >= FI.SEUIL_SAIT for o in obs):
            tours = 1
        echeance = t0 + (DELAI_VOIX if voix else DELAI_TEXTE)
        finale: dict = {}
        # 3. Boucle : le modèle choisit les outils, le Guardian décide
        for tour in range(tours):
            res["tours"] = tour + 1
            reste = tours - tour
            prompt = PROMPT.format(maintenant=f"{jj[m.weekday()]} {m.strftime('%d/%m/%Y %H:%M')}", qui=qui,
                                   tours=f"{reste} (DERNIER tour : tu dois repondre)" if reste == 1 else reste,
                                   outils=catalogue, observations=self._rendu(obs), question=q)
            restant = echeance - time.monotonic()
            if restant < MARGE_MIN:
                res["delai_depasse"] = True
                etape("ia_non_lancee", time.monotonic(), tour=tour + 1, restant=round(restant, 1))
                break
            t_ia = time.monotonic()
            try:
                rep = await asyncio.wait_for(self.core.routeur.demander(
                    prompt, "brain", "adria_core_brain", **({"seulement": seulement} if seulement else {})),
                    restant)
            except asyncio.TimeoutError:
                res["delai_depasse"] = True  # réponse rendue sans nouvel appel IA, avec ce qui est déjà trouvé
                etape("ia", t_ia, tour=tour + 1, ok=False, raison=f"délai de la mission atteint ({round(restant, 1)} s)")
                break
            etape("ia", t_ia, tour=tour + 1, ok=bool(rep.get("ok")), moteur=rep.get("moteur"),
                  **({} if rep.get("ok") else {"raison": str(rep.get("essais") or "")[:200]}))
            if not rep.get("ok"):
                break
            res["moteur"] = rep.get("moteur")
            j = lire_json(rep.get("data") or "")
            if j.get("action") == "outil" and tour < tours - 1:
                nom, args = str(j.get("outil") or ""), j.get("args") if isinstance(j.get("args"), dict) else {}
                if nom not in OUTILS:
                    obs.append({"ref": None, "outil": nom, "texte": "outil inconnu, refusé", "confiance": 0})
                    continue
                if nom in SANS_ARGUMENT and nom in res["outils"]:  # v0.22.4 : pas de double appel
                    obs.append({"ref": None, "outil": nom, "texte": "déjà consulté : utilise l'observation existante",
                                "confiance": 0})
                    continue
                d = await self.guardian.decider(nom, profil, args, mission)
                if not d.autorise:
                    obs.append({"ref": None, "outil": nom, "texte": f"refusé par le Guardian : {d.raison}", "confiance": 0})
                    continue
                try:
                    t_out = time.monotonic()
                    self._observer(obs, nom, await self.outil(nom, args, profil, q, banc), numero)
                    res["outils"].append(nom)
                    etape(f"outil:{nom}", t_out)
                except Exception as e:
                    obs.append({"ref": None, "outil": nom, "texte": f"erreur : {type(e).__name__}", "confiance": 0})
                continue
            if j.get("action") == "repondre" or j.get("reponse"):
                finale = j
                break
            if j.get("action") == "outil":  # dernier tour : on force une réponse au tour suivant -> pas de tour
                break
        return fin(**await self._verifier(finale, obs, profil, mission, banc))

    # ------------------------------------------------------------------ vérification (sans IA)
    async def _verifier(self, j: dict, obs: list[dict], profil: str, mission: str, banc: bool = False) -> dict:
        refs = {o["ref"]: o for o in obs if o.get("ref")}
        citees = [refs[r] for r in (j.get("preuves") or []) if isinstance(r, str) and r in refs]
        preuves = pertinentes(str(j.get("reponse") or ""), citees)
        memoire = [o for o in obs if o.get("ref", "") and o["ref"].startswith("M")]
        utiles = preuves or [o for o in obs if o.get("ref") and o.get("confiance", 0) > 0]
        st = FI.statut(utiles)
        k: dict = {"statut": st}
        cont = FI.contradictions([{**o, "texte": o["texte"]} for o in memoire])
        if cont:
            if not banc:  # le banc n'écrit pas dans le registre des contradictions
                self.registre.noter(cont)
            k["contradictions"] = cont[:3]
        if len(preuves) < len(citees):
            k["preuves_ecartees"] = [o["ref"] for o in citees if o not in preuves]
        texte = str(j.get("reponse") or "").strip()
        general = j.get("type") == "general"
        if not texte:  # pas de réponse du modèle (IA en panne ou délai dépassé) : repli honnête, 0 IA
            # v0.22.3 : ce que les OUTILS fiables ont vérifié passe d'abord, puis la mémoire
            outils = [o for o in obs if str(o.get("ref") or "").startswith("O") and o.get("confiance", 0) >= FI.SEUIL_SAIT]
            if outils:
                texte = "Voici ce que j'ai vérifié : " + " ; ".join(resume_outil(o) for o in outils[:2]) + "."
                preuves = outils[:2]
                if memoire and st != "ignore":
                    lignes = sorted(memoire, key=lambda o: -o["confiance"])[:2]
                    texte += " En mémoire : " + " ; ".join(o["texte"][:120] for o in lignes)
                    preuves += lignes
                k["statut"] = FI.statut(preuves)
            elif memoire and st != "ignore":
                lignes = sorted(memoire, key=lambda o: -o["confiance"])[:3]
                texte = "Voici ce que j'ai trouvé : " + " ; ".join(
                    f"{o['date'] + ' : ' if o['date'] else ''}{o['texte'][:160]}" for o in lignes)
                preuves = lignes
            else:
                texte, k["statut"] = NE_SAIS_PAS, "ignore"
        elif not general:
            # réponse « maison » : jugée sur ses preuves citées (à défaut, sur ce qui a été observé)
            # preuves citées mais toutes hors sujet : la réponse n'est pas justifiée -> au mieux « suppose »
            st = FI.statut(preuves or ([] if citees else utiles))
            if citees and not preuves and st == "ignore" and utiles:
                st = "suppose"
            k["statut"] = st
            if st == "ignore":
                texte = NE_SAIS_PAS  # aucune preuve : on ne laisse pas passer
            elif st == "suppose":
                texte = "Je n'en suis pas sûre : " + texte[0].lower() + texte[1:]
        if general and not preuves:
            k["statut"] = "general"
        if cont:
            texte += " Attention : deux souvenirs se contredisent, j'ai retenu le plus récent."
        prop = j.get("proposition")
        if prop and isinstance(prop, (str, dict)):
            t = prop if isinstance(prop, str) else json.dumps(prop, ensure_ascii=False)
            d = await self.guardian.decider("proposer_action", profil, {"texte": t}, mission)
            if d.autorise:
                k["proposition"] = t[:300]
                texte += " Je te propose : " + t[:200] + " (rien n'est fait sans ton OUI)."
            else:
                k["proposition_refusee"] = d.raison
        k["preuves"] = [{"ref": o["ref"], "date": o.get("date") or "", "source": o.get("source"),
                         "origine": libelle(str(o.get("source") or "")),
                         "extrait": o["texte"][:160], "confiance": o.get("confiance"), "nature": o.get("nature")}
                        for o in preuves[:6]]
        return {"texte": texte[:900], **k}

    # ------------------------------------------------------------------ suivi
    def _garder(self, res: dict, q: str, m: datetime) -> None:
        self.dernier = {"quand": m.isoformat(timespec="seconds"), "question": q[:120], "profil": res["profil"],
                        "ok": res["ok"], "statut": res["statut"], "outils": res["outils"], "tours": res["tours"],
                        "moteur": res["moteur"], "duree": res.get("duree"), "refus": res.get("refus"),
                        "delai_depasse": bool(res.get("delai_depasse")), "etapes": res.get("etapes", [])[:12]}
        LOG.info("BRAIN %s s, %s : %s", res.get("duree"), res["statut"],
                 " | ".join(f"{e['etape']} {e['s']} s" + (f" ({e.get('moteur') or e.get('raison') or ''})"
                                                          if e["etape"] == "ia" else "") for e in res.get("etapes", [])))
        h = self._meta("brain_missions", [])
        h.append(self.dernier)
        self.core.memoire.meta("brain_missions", json.dumps(h[-20:], ensure_ascii=False))

    def _ancrage(self) -> dict | None:
        """Révision 6 : état du registre d'ancrage (lecture seule)."""
        reg = getattr(self.guardian, "registre", None)
        if reg is None:
            return None
        try:
            v = reg.verifier(self.core.memoire.db)
        except Exception as e:
            v = {"etat": "inaccessible", "motif": type(e).__name__}
        return {"etat": v.get("etat"), "motif": v.get("motif"), "garantie_depuis": v.get("garantie_depuis"),
                "ancrage_en_retard": self.guardian.ancrage_en_retard, "dernier_echec": reg.dernier_echec}

    def trancher(self, data: dict) -> dict:
        """v0.22.2 : Mael marque des contradictions comme « tranchées » (liste d'ids). Rien n'est supprimé."""
        ids = data.get("ids") if isinstance(data.get("ids"), list) else [data.get("id")]
        faits = 0
        for i in ids:
            try:
                faits += bool(self.registre.trancher(int(i)))
            except (TypeError, ValueError):
                continue
        return {"req_id": data.get("req_id", ""), "ok": True, "tranchees": faits,
                "ouvertes": len(self.registre.ouvertes(1000))}

    def etat(self) -> dict:
        g = self.guardian.stats()
        return {"state": (self.dernier or {}).get("statut") or "aucune",
                "attributes": {"friendly_name": "ADRIA Core — BRAIN", "icon": "mdi:brain",
                               "derniere_mission": self.dernier, "missions_jour": self._meta("brain_compte", {}).get("n", 0),
                               "guardian": g, "journal_integre": self.guardian.verifier_chaine()["ok"],
                               "journal_ancre": self.guardian.ancre(),
                               "ancrage": self._ancrage(),
                               "contradictions_ouvertes": len(self.registre.ouvertes(1000)),
                               # v0.22.2 : les 20 plus récentes, à trancher par Mael (textes abrégés)
                               "contradictions": [
                                   {"id": c["id"], "quand": c["quand"][:16], "motif": c["motif"],
                                    "a": c["texte_a"][:120], "b": c["texte_b"][:120]}
                                   for c in self.registre.ouvertes(20)]}}
