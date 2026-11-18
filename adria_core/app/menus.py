"""Menus de la semaine et courses (lot M1).

- Dimanche 17h15 (ou à la voix « propose des menus ») : 1 appel IA propose les dîners des 7 jours
  suivants, selon le quart de Mael, les activités de l'agenda (soirs chargés = plat rapide), les goûts
  retenus ([gouts] du savoir) et sans reprendre les plats des 3 dernières semaines.
- Notification : OUI (garde les menus et ajoute les courses à la liste) / Autres idées / NON.
  Rien n'est ajouté sans OUI.
- Chaque jour à 16h30 : « 🍽️ Ce soir : … » si des menus ont été validés.
- À la voix : « qu'est-ce qu'on mange ce soir / demain / cette semaine ? » -> réponse 0 IA.
Aucune ligne n'est jamais effacée (un nouveau menu pour un jour remplace l'ancien à la lecture).
Actif si input_boolean.adria_core_menus est ON.
"""

import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta

from store import normaliser

LOG = logging.getLogger("adria_core.menus")

INTERRUPTEUR = "input_boolean.adria_core_menus"
LISTE_COURSES = "todo.liste_dachats"
CALENDRIERS = ("calendar.famille_2", "calendar.mael_adria")
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MAX_COURSES = 25
PROMPT = (
    "Tu es ADRIA, la majordome de la famille Kerherve (Saint-Brieuc, Bretagne ; 2 adultes, Ryan au lycee, "
    "Iwan au college, Aelia en CP). Propose les DINERS des 7 jours suivants : {jours}. Cuisine familiale "
    "simple, de saison ({mois}), equilibree, budget raisonnable, que les enfants aiment. Les soirs marques "
    "CHARGE : plat rapide (20 minutes maximum) ou prepare a l avance. Varie viande, poisson, oeufs, "
    "vegetarien ; un plat plaisir le week-end. Quart de Mael cette semaine : {quart} (matin = leve a 3h, "
    "il dine tot ; nuit = il part a 19h10 ; apres-midi = il rentre vers 22h45, il dine en decale). "
    "Gouts de la famille : {gouts}. Plats a NE PAS reprendre (deja servis recemment) : {recents}. "
    "Soirs : {soirs}. Reponds UNIQUEMENT par un JSON : {{\"jours\": [{{\"date\": \"AAAA-MM-JJ\", "
    "\"plat\": \"nom du plat, 8 mots maximum\", \"rapide\": true ou false}}], \"courses\": [\"ingredient a "
    "acheter, quantite pour 5 personnes, ex. 1 kg de carottes\"]}}. Courses : seulement ce qu il faut "
    "acheter (pas sel, poivre, huile, farine, sucre), regroupe, 25 lignes maximum."
)


# ------------------------------------------------------------------ outils purs (testés)

def jours_a_venir(debut: date, n: int = 7) -> list[date]:
    return [debut + timedelta(days=k) for k in range(n)]


def libelle_jour(j: date) -> str:
    return f"{JOURS[j.weekday()]} {j.strftime('%d/%m')}"


def analyser(d, jours: list[date]) -> dict | None:
    """JSON de l'IA -> {jours: {date: (plat, rapide)}, courses: [..]} ou None si inutilisable."""
    if not isinstance(d, dict) or not isinstance(d.get("jours"), list):
        return None
    attendus = {j.isoformat() for j in jours}
    menus: dict[str, tuple[str, bool]] = {}
    for x in d["jours"]:
        if not isinstance(x, dict):
            continue
        jour = str(x.get("date") or "")[:10]
        plat = re.sub(r"\s+", " ", str(x.get("plat") or "")).strip(" .")[:70]
        if jour in attendus and plat:
            menus[jour] = (plat, bool(x.get("rapide")))
    if len(menus) < max(3, len(jours) - 2):
        return None
    courses = []
    for c in d.get("courses") or []:
        c = re.sub(r"\s+", " ", str(c)).strip(" .-•")[:60]
        if c and c.lower() not in {x.lower() for x in courses}:
            courses.append(c)
    return {"jours": dict(sorted(menus.items())), "courses": courses[:MAX_COURSES]}


def texte_menus(menus: dict) -> str:
    return "\n".join(f"• {libelle_jour(date.fromisoformat(j))} : {p}{' ⚡' if r else ''}"
                     for j, (p, r) in menus["jours"].items())


def soirs_charges(evenements: list[dict], jours: list[date]) -> dict[str, list[str]]:
    """Événements de l'agenda qui tombent entre 17h et 21h (ou toute la journée), par jour."""
    out: dict[str, list[str]] = {j.isoformat(): [] for j in jours}
    for e in evenements:
        debut = str(e.get("start") or "")
        jour = debut[:10]
        if jour not in out:
            continue
        heure = debut[11:16]
        if not heure or "17:00" <= heure <= "21:00":
            titre = re.sub(r"\s+", " ", str(e.get("summary") or "")).strip()[:40]
            if titre and titre not in out[jour]:
                out[jour].append(titre)
    return out


def a_ajouter(courses: list[str], deja: list[str]) -> list[str]:
    exist = {normaliser(x).strip() for x in deja}
    return [c for c in courses if normaliser(c).strip() not in exist]


def quel_jour(question: str, aujourdhui: date) -> list[date]:
    t = normaliser(question)
    if "semaine" in t:
        return jours_a_venir(aujourdhui, 7)
    if "apres-demain" in t or "apres demain" in t:
        return [aujourdhui + timedelta(days=2)]
    if "demain" in t:
        return [aujourdhui + timedelta(days=1)]
    for i, nom in enumerate(JOURS):
        if re.search(rf"\b{nom}\b", t):
            return [aujourdhui + timedelta(days=(i - aujourdhui.weekday()) % 7)]
    return [aujourdhui]


def repondre(question: str, plats: dict[str, str], aujourdhui: date) -> str:
    jours = quel_jour(question, aujourdhui)
    trouves = [(j, plats.get(j.isoformat())) for j in jours]
    if not any(p for _, p in trouves):
        return ("Je n'ai pas encore de menu pour ce jour-là. Dis-moi « propose des menus » et je t'envoie "
                "des idées pour la semaine.")
    if len(jours) == 1:
        j, p = trouves[0]
        quand = "Ce soir" if j == aujourdhui else ("Demain" if j == aujourdhui + timedelta(days=1)
                                                      else f"{JOURS[j.weekday()].capitalize()}")
        return f"{quand}, c'est {p}."
    return "Cette semaine : " + " ; ".join(f"{JOURS[j.weekday()]} {p}" for j, p in trouves if p) + "."


# --------------------------------------------------------------------------- l'agent

class AgentMenus:
    def __init__(self, core, notifier: str) -> None:
        self.core = core
        self.notifier = notifier
        self.db = core.memoire.db
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS menus (
                id INTEGER PRIMARY KEY, jour TEXT NOT NULL, plat TEXT NOT NULL, rapide INTEGER, ajoute_le TEXT NOT NULL
            );
            """)
        self.db.commit()
        self.dernier: dict | None = None

    # ---- stockage
    def plats(self) -> dict[str, str]:
        """Dernier plat validé pour chaque jour."""
        return {r["jour"]: r["plat"] for r in self.db.execute("SELECT jour, plat FROM menus ORDER BY id")}

    def recents(self, aujourdhui: date, jours: int = 21) -> list[str]:
        debut = (aujourdhui - timedelta(days=jours)).isoformat()
        return sorted({p for j, p in self.plats().items() if j >= debut})

    def garder(self, menus: dict) -> None:
        quand = datetime.now().isoformat(timespec="seconds")
        for j, (p, r) in menus["jours"].items():
            self.db.execute("INSERT INTO menus (jour, plat, rapide, ajoute_le) VALUES (?, ?, ?, ?)",
                            (j, p, int(r), quand))
        self.db.commit()
        self.core.memoire.ajouter("menus", [{"summary": f"Menu du {libelle_jour(date.fromisoformat(j))} : {p}",
                                             "date": j} for j, (p, _) in menus["jours"].items()])

    def resume(self, aujourdhui: date | None = None) -> str:
        j = aujourdhui or date.today()
        p = self.plats()
        l = [f"{libelle_jour(x)} {p[x.isoformat()]}" for x in jours_a_venir(j, 7) if x.isoformat() in p]
        return " ; ".join(l) if l else "aucun menu validé"

    def _lire(self, cle, defaut):
        try:
            return json.loads(self.core.memoire.meta(cle) or "null") or defaut
        except ValueError:
            return defaut

    def _ecrire(self, cle, valeur) -> None:
        self.core.memoire.meta(cle, json.dumps(valeur, ensure_ascii=False))

    async def notifier_tel(self, titre, message, actions=None, tag="adria_menus") -> None:
        domaine, service = self.notifier.split(".", 1)
        data = {"tag": tag, "channel": "ADRIA famille"}
        if actions:
            data["actions"] = actions
            data["sticky"] = "true"
        await self.core.appel("POST", f"/services/{domaine}/{service}",
                              {"title": titre, "message": message, "data": data})

    # ---- contexte
    async def gouts(self) -> str:
        try:
            rep = await self.core.appel("POST", "/services/todo/get_items?return_response",
                                        {"entity_id": "todo.adria_savoir"})
            items = rep.get("service_response", {}).get("todo.adria_savoir", {}).get("items", [])
            g = [i["summary"][8:].strip() for i in items if str(i.get("summary", "")).startswith("[gouts]")]
            return " ; ".join(g)[:700] or "non renseignes"
        except Exception:
            return "non renseignes"

    async def agenda(self, jours: list[date]) -> list[dict]:
        evts: list[dict] = []
        debut = datetime.combine(jours[0], datetime.min.time()).isoformat()
        fin = datetime.combine(jours[-1] + timedelta(days=1), datetime.min.time()).isoformat()
        for cal in CALENDRIERS:
            try:
                rep = await self.core.appel("POST", "/services/calendar/get_events?return_response",
                                            {"entity_id": cal, "start_date_time": debut, "end_date_time": fin})
                evts += (rep.get("service_response", {}).get(cal) or {}).get("events", [])
            except Exception:
                pass
        return evts

    # ---- proposition
    async def proposer(self, forcer: bool = False, essai: int = 0) -> dict:
        auj = date.today()
        res = {"quand": datetime.now().isoformat(timespec="seconds"), "ok": False}
        if await self.core.etat(INTERRUPTEUR) != "on":
            res["texte"] = "Les menus de la semaine sont éteints."
            return res
        if not forcer and auj.weekday() != 6:
            res["texte"] = "pas dimanche"
            return res
        jours = jours_a_venir(auj + timedelta(days=1), 7)
        charges = soirs_charges(await self.agenda(jours), jours)
        soirs = " ; ".join(f"{libelle_jour(date.fromisoformat(j))}" + (f" CHARGE ({', '.join(e)})" if e else "")
                           for j, e in charges.items())
        prompt = PROMPT.format(jours=", ".join(f"{libelle_jour(j)} ({j.isoformat()})" for j in jours),
                               mois=auj.strftime("%m/%Y"), quart=await self.core.etat("input_select.quart_mael"),
                               gouts=await self.gouts(), recents=", ".join(self.recents(auj) + [
                                   r["plat"] for r in self._lire("menus_refuses", [])
                                   if r.get("le", "") >= (auj - timedelta(days=7)).isoformat()]) or "aucun",
                               soirs=soirs)
        rep = await self.core.routeur.demander(prompt, "rapide", "adria_core_menus")
        import formulaires as F
        menus = analyser(F.lire_json(rep.get("data") or ""), jours) if rep.get("ok") else None
        if not menus:
            res["texte"] = "Je n'ai pas réussi à préparer les menus (IA indisponible). Réessaie plus tard."
            await self.notifier_tel("🍽️ Menus de la semaine", res["texte"])
            return res
        ident = uuid.uuid4().hex[:8]
        attentes = {ident: {"menus": menus, "essai": essai}}  # une seule proposition en attente
        self._ecrire("menus_attentes", attentes)
        await self.notifier_tel(
            "🍽️ Menus de la semaine",
            texte_menus(menus) + f"\n\n🛒 {len(menus['courses'])} article(s) à acheter. ⚡ = plat rapide."
            "\nOUI : je garde les menus et j'ajoute les courses à la liste.",
            [{"action": f"ADRIA_MENU_{ident}_OUI", "title": "OUI"},
             {"action": f"ADRIA_MENU_{ident}_AUTRES", "title": "Autres idées"},
             {"action": f"ADRIA_MENU_{ident}_NON", "title": "NON"}], tag="adria_menus")
        res.update(ok=True, texte="Je t'ai envoyé les menus de la semaine sur ton téléphone : dis OUI pour "
                                  "les garder et ajouter les courses.")
        self.dernier = {"propose": res["quand"], "etat": "en attente"}
        return res

    async def recevoir_action(self, data: dict) -> str | None:
        action = str(data.get("action") or "")
        if not action.startswith("ADRIA_MENU_"):
            return None
        _, _, ident, choix = action.split("_", 3)
        attentes = self._lire("menus_attentes", {})
        p = attentes.pop(ident, None)
        if not p:
            return None
        self._ecrire("menus_attentes", attentes)
        if choix == "AUTRES":
            if p.get("essai", 0) >= 2:
                await self.notifier_tel("🍽️ Menus", "Trois propositions déjà faites : dis-moi à la voix ce "
                                        "qui te tente et je ferai mieux la prochaine fois.")
                return choix
            # les plats refusés passent dans les « déjà servis » pour ne pas revenir
            refus = self._lire("menus_refuses", [])
            refus += [{"plat": pl, "le": date.today().isoformat()} for pl, _ in p["menus"]["jours"].values()]
            self._ecrire("menus_refuses", refus[-60:])
            await self.proposer(forcer=True, essai=p.get("essai", 0) + 1)
            return choix
        if choix != "OUI":
            self.dernier = {"etat": "refusés"}
            return choix
        menus = p["menus"]
        self.garder(menus)
        n = 0
        try:
            rep = await self.core.appel("POST", "/services/todo/get_items?return_response",
                                        {"entity_id": LISTE_COURSES, "status": "needs_action"})
            deja = [i["summary"] for i in rep.get("service_response", {}).get(LISTE_COURSES, {}).get("items", [])]
            for c in a_ajouter(menus["courses"], deja):
                await self.core.appel("POST", "/services/todo/add_item", {"entity_id": LISTE_COURSES, "item": c})
                n += 1
        except Exception as e:
            LOG.warning("Courses : %s", str(e)[:120])
        await self.notifier_tel("✅ Menus gardés", f"{len(menus['jours'])} dîners prévus, {n} article(s) ajouté(s) "
                                "à la liste de courses. Rappel du plat chaque jour à 16h30.")
        self.dernier = {"etat": "validés", "courses_ajoutees": n,
                        "le": datetime.now().isoformat(timespec="seconds")}
        return choix

    async def ce_soir(self) -> None:
        if await self.core.etat(INTERRUPTEUR) != "on":
            return
        p = self.plats().get(date.today().isoformat())
        if p:
            await self.notifier_tel("🍽️ Ce soir", f"Au menu : {p}.", tag="adria_menu_soir")

    async def voix(self, data: dict) -> dict:
        q = str(data.get("question") or "")
        res = {"req_id": data.get("req_id", ""), "ok": True}
        if re.search(r"(propose|prepare|fais|trouve|donne)[- ]?(moi|nous)?\s*(des|les|un)?\s*(menu|idees? de repas)",
                     normaliser(q)):
            r = await self.proposer(forcer=True)
            res["texte"] = r.get("texte") or "C'est fait."
            return res
        if await self.core.etat(INTERRUPTEUR) != "on":
            res["texte"] = "Les menus de la semaine sont éteints."
            return res
        res["texte"] = repondre(q, self.plats(), date.today())
        return res
