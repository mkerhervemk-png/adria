"""Cerveau général d'ADRIA (lot G1) : pour tout ce qu'aucune compétence ne couvre.

Déclenché par avatar_demander :
- demande explicite (« réfléchis », « aide-moi à », « rédige », « calcule », « conseille-moi »,
  « organise », « compare »…) -> événement `adria_core_general` {question, qui, voix, req_id}, réponse
  attendue ;
- ou en secours, quand la réponse normale d'ADRIA est « je ne sais pas » -> même événement avec
  asynchrone=true : le Core répond lui-même (Echo qui a parlé + téléphone de Mael).
Le Core rassemble le contexte sans IA (mémoire du Core, agenda 7 jours, budget, menus, abonnements,
état des appareils cités) puis fait 1 appel IA « profond » (Claude si actif, sinon Gemini…).
L'IA peut PROPOSER une action, jamais la faire : courses, rappel, rendez-vous dans l'agenda ou mémo à
quelqu'un de la maison. Notification OUI / NON à Mael ; rien sans OUI (et jamais pour un enfant).
Limite : 30 questions par jour. Actif si input_boolean.adria_core_general est ON.
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta

from competences import choisir_entites

LOG = logging.getLogger("adria_core.general")

INTERRUPTEUR = "input_boolean.adria_core_general"
MAX_JOUR = 30
SECOURS_ATTENTE = 12
ADULTES = {"mael", "vanessa"}
FOYER = {"mael", "vanessa", "ryan", "iwan", "aelia"}
SOURCES_ENFANT = ("adria_souvenirs", "adria_jarvis_episodes", "adria_journal_interieur", "souvenirs_famille",
                  "menus")
TYPES = ("courses", "rappel", "agenda", "memo")
PROMPT = (
    "Tu es ADRIA, l intelligence de la Maison Kerherve (Saint-Brieuc) : a la fois majordome, comptable du "
    "foyer, nourrice bienveillante, secretaire et conseillere. {qui_parle}. Nous sommes le {maintenant}. "
    "Reponds a la demande en t appuyant d abord sur le CONTEXTE de la maison ci-dessous ; pour le reste, "
    "utilise tes connaissances generales. N invente jamais un fait sur la maison ou la famille : s il "
    "n est pas dans le CONTEXTE, dis que tu ne le sais pas. Argent, sante, droit : donne des informations "
    "factuelles et dis quand il vaut mieux demander a un professionnel. Tutoie toujours la personne. "
    "Si tu fais une proposition, ne demande pas dans la reponse si elle la veut : dis simplement ce que tu "
    "proposes (elle recevra un bouton OUI). Pour un texte a rediger (lettre, "
    "mail, message), ecris-le en entier dans reponse_detaillee. {consigne_enfant}"
    "Si une action concrete aiderait (ajouter des courses, un rappel, un rendez-vous dans l agenda, un memo "
    "a quelqu un de la maison), PROPOSE-LA dans proposition (elle ne sera faite qu apres le OUI de Mael) ; "
    "sinon proposition = null. Reponds UNIQUEMENT par un JSON : {{\"reponse_orale\": \"2 a 4 phrases pour "
    "une enceinte, sans symbole ni liste\", \"reponse_detaillee\": \"reponse complete, 1500 caracteres "
    "maximum, texte simple\", \"sources\": \"contexte maison et/ou connaissances generales\", "
    "\"proposition\": null ou {{\"type\": \"courses\", \"articles\": [\"...\"]}} ou {{\"type\": \"rappel\", "
    "\"quand\": \"AAAA-MM-JJ HH:MM\", \"texte\": \"...\"}} ou {{\"type\": \"agenda\", \"debut\": "
    "\"AAAA-MM-JJ HH:MM\", \"fin\": \"AAAA-MM-JJ HH:MM\", \"titre\": \"...\"}} ou {{\"type\": \"memo\", "
    "\"pour\": \"mael|vanessa|ryan|iwan|aelia\", \"texte\": \"...\"}}}}.\n"
    "CONTEXTE :\n{contexte}\nDEMANDE : {question}"
)


# ------------------------------------------------------------------ outils purs (testés)

def prenom(qui: str) -> str:
    p = str(qui or "").split(" ")[0].lower().replace("ë", "e").replace("é", "e")
    return p if p in FOYER else "inconnu"


def _dt(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v or "").strip().replace(" ", "T")[:16])
    except ValueError:
        return None


def verifier_proposition(p, maintenant: datetime) -> dict | None:
    """Proposition de l'IA -> proposition propre (liste blanche), ou None."""
    if not isinstance(p, dict) or p.get("type") not in TYPES:
        return None
    t = p["type"]
    limite = maintenant + timedelta(days=366)
    if t == "courses":
        art = [re.sub(r"\s+", " ", str(a)).strip()[:60] for a in (p.get("articles") or []) if str(a).strip()]
        return {"type": t, "articles": art[:15]} if art else None
    if t == "rappel":
        q, txt = _dt(p.get("quand")), str(p.get("texte") or "").strip()[:200]
        return {"type": t, "quand": q.isoformat(timespec="minutes"), "texte": txt} \
            if q and maintenant < q < limite and txt else None
    if t == "agenda":
        d, f, titre = _dt(p.get("debut")), _dt(p.get("fin")), str(p.get("titre") or "").strip()[:100]
        if not d or not titre or not maintenant < d < limite:
            return None
        if not f or f <= d or f - d > timedelta(hours=12):
            f = d + timedelta(hours=1)
        return {"type": t, "debut": d.isoformat(timespec="minutes"), "fin": f.isoformat(timespec="minutes"),
                "titre": titre}
    pour, txt = str(p.get("pour") or "").lower(), str(p.get("texte") or "").strip()[:200]
    return {"type": t, "pour": pour, "texte": txt} if pour in FOYER and txt else None


def libelle_proposition(p: dict) -> str:
    if p["type"] == "courses":
        return "🛒 Ajouter aux courses : " + ", ".join(p["articles"])
    if p["type"] == "rappel":
        return f"⏰ Rappel le {datetime.fromisoformat(p['quand']).strftime('%d/%m à %Hh%M')} : {p['texte']}"
    if p["type"] == "agenda":
        return f"📅 Agenda le {datetime.fromisoformat(p['debut']).strftime('%d/%m à %Hh%M')} : {p['titre']}"
    return f"📝 Mémo pour {p['pour'].capitalize()} : {p['texte']}"


def nettoyer(t: str, n: int) -> str:
    t = re.sub(r"[*#_`]", "", str(t or "")).strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


# --------------------------------------------------------------------------- l'agent

class AgentGeneral:
    def __init__(self, core, notifier: str) -> None:
        self.core = core
        self.notifier = notifier
        self.dernier: dict | None = None

    def _lire(self, cle, defaut):
        try:
            return json.loads(self.core.memoire.meta(cle) or "null") or defaut
        except ValueError:
            return defaut

    def _ecrire(self, cle, valeur) -> None:
        self.core.memoire.meta(cle, json.dumps(valeur, ensure_ascii=False))

    async def notifier_tel(self, titre, message, actions=None, tag="adria_general") -> None:
        domaine, service = self.notifier.split(".", 1)
        data = {"tag": tag, "channel": "ADRIA famille"}
        if actions:
            data["actions"] = actions
            data["sticky"] = "true"
        await self.core.appel("POST", f"/services/{domaine}/{service}",
                              {"title": titre, "message": message[:3500], "data": data})

    def compter(self, m: datetime) -> bool:
        c = self._lire("general_compte", {})
        if c.get("date") != m.date().isoformat():
            c = {"date": m.date().isoformat(), "n": 0}
        if c["n"] >= MAX_JOUR:
            return False
        c["n"] += 1
        self._ecrire("general_compte", c)
        return True

    async def contexte(self, question: str, p: str) -> str:
        adulte = p in ADULTES
        blocs = []
        lignes = self.core.memoire.chercher(question, 8)
        if not adulte:
            lignes = [l for l in lignes if l["source"] in SOURCES_ENFANT or l["source"].startswith("archive/souvenir")]
        if lignes:
            blocs.append("MEMOIRE : " + " ; ".join(f"[{l['date'] or '?'}] {l['texte'][:200]}" for l in lignes))
        try:
            blocs.append("MENUS : " + self.core.menus.resume())
        except Exception:
            pass
        auj = date.today()
        evts = await self.core.menus.agenda([auj + timedelta(days=k) for k in range(7)])
        if evts:
            blocs.append("AGENDA 7 JOURS : " + " ; ".join(
                f"{str(e.get('start'))[:16].replace('T', ' ')} {e.get('summary')}" for e in evts[:25]))
        blocs.append(f"QUART DE MAEL : {await self.core.etat('input_select.quart_mael')}")
        if adulte:
            try:
                ab = self.core.suivis.etat()
                blocs.append(f"ABONNEMENTS : {ab['state']} €/mois : " + " ; ".join(
                    f"{a['fournisseur']} {a['montant']} € {a['periodicite']}" for a in ab["attributes"]["abonnements"]))
                blocs.append("BUDGET : " + self.core.budget.resume(float(ab["state"])))
            except Exception:
                pass
            try:
                etats = await self.core.appel("GET", "/states")
                par_id = {e["entity_id"]: e for e in etats}
                ent = choisir_entites(question, etats, 12)
                if ent:
                    blocs.append("APPAREILS CITES : " + " ; ".join(
                        f"{n} = {par_id.get(e, {}).get('state', '?')}" for e, n in ent if e in par_id))
            except Exception:
                pass
        return "\n".join(blocs)[:5000]

    async def repondre(self, data: dict, maintenant: datetime | None = None) -> dict:
        m = maintenant or datetime.now()
        res = {"req_id": data.get("req_id", ""), "ok": False, "texte": ""}
        p = prenom(data.get("qui"))
        enfant = p not in ADULTES or str(data.get("enfant")).lower() == "true"
        if await self.core.etat(INTERRUPTEUR) != "on":
            res["texte"] = "Mon cerveau général est éteint pour le moment."
            return res
        if p == "inconnu":
            res["texte"] = "Je ne réponds à ce genre de demande qu'aux membres de la famille."
            return res
        if not self.compter(m):
            res["texte"] = "J'ai atteint ma limite de réflexions approfondies pour aujourd'hui. Redemande-moi demain."
            return res
        q = str(data.get("question") or "").strip()[:800]
        jj = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        prompt = PROMPT.format(
            qui_parle=f"C est {p.capitalize()} qui te parle" + (" (un enfant)" if enfant else ""),
            maintenant=f"{jj[m.weekday()]} {m.strftime('%d/%m/%Y %H:%M')}",
            consigne_enfant=("Tu parles a un enfant : langage simple, bienveillant, jamais d argent, de securite "
                             "ni de reglages de la maison, aucune proposition (proposition = null). ") if enfant else "",
            contexte=await self.contexte(q, "enfant" if enfant else p), question=q)
        # à la voix : chaîne rapide (réponse en moins de 20 s) ; au téléphone : chaîne profonde
        voix = str(data.get("voix")).lower() == "true"
        rep = await self.core.routeur.demander(prompt, "interactif" if voix else "profond", "adria_core_general")
        if not rep.get("ok"):
            res["texte"] = "Je n'arrive pas à réfléchir pour l'instant, mes IA ne répondent pas. Réessaie plus tard."
            return res
        import formulaires as F
        d = F.lire_json(rep.get("data") or "")
        orale = nettoyer(d.get("reponse_orale") or rep.get("data"), 700)
        detail = nettoyer(d.get("reponse_detaillee") or "", 3000)
        res.update(ok=True, texte=orale, detail=detail, moteur=rep.get("moteur"),
                   sources=str(d.get("sources") or "")[:100])
        self.core.memoire.ajouter("general", [{"summary": f"Question de {p.capitalize()} : {q[:150]} -> {orale[:250]}",
                                               "date": m.date().isoformat()}])
        prop = None if enfant else verifier_proposition(d.get("proposition"), m)
        if prop:
            ident = uuid.uuid4().hex[:8]
            att = self._lire("general_attentes", {})
            att[ident] = {"prop": prop, "qui": p, "le": m.isoformat(timespec="seconds")}
            self._ecrire("general_attentes", dict(list(att.items())[-10:]))
            await self.notifier_tel("🧠 ADRIA propose", f"Suite à la demande de {p.capitalize()} :\n"
                                    f"{libelle_proposition(prop)}\nJe le fais ?",
                                    [{"action": f"ADRIA_GEN_{ident}_OUI", "title": "OUI"},
                                     {"action": f"ADRIA_GEN_{ident}_NON", "title": "NON"}], tag=f"adria_gen_{ident}")
            res["texte"] = orale + (" Je t'ai envoyé la proposition sur ton téléphone." if p == "mael"
                                    else " J'ai envoyé la proposition sur le téléphone de Mael.")
            res["proposition"] = libelle_proposition(prop)
        res["detail_envoye"] = bool(detail and len(detail) > len(orale) + 40 and not enfant)
        if res["detail_envoye"]:
            await self.notifier_tel(f"🧠 ADRIA — {nettoyer(q, 60)}", detail
                                    + (f"\n[{res['sources']}]" if res.get("sources") else ""))
        self.dernier = {"quand": m.isoformat(timespec="seconds"), "qui": p, "moteur": rep.get("moteur"),
                        "proposition": bool(prop)}
        return res

    async def secours(self, data: dict) -> None:
        """Réponse en arrière-plan : « je ne sais pas » d'ADRIA (le Core cherche plus loin) ou demande
        faite au téléphone (mode=telephone : ADRIA n'est pas bloquée, la réponse arrive en notification)."""
        res = await self.repondre(data)
        if str(data.get("mode")) == "telephone":
            if not res.get("ok") or not res.get("detail_envoye"):
                await self.notifier_tel("🧠 ADRIA — " + nettoyer(data.get("question"), 60), res.get("texte") or "")
            return
        if not res.get("ok"):
            return
        await asyncio.sleep(SECOURS_ATTENTE)  # laisser ADRIA finir de dire « je ne sais pas »
        echo = str(data.get("echo") or "")
        texte = "J'ai cherché plus loin. " + res["texte"]
        if str(data.get("voix")).lower() == "true" and echo.startswith("media_player."):
            try:
                await self.core.appel("POST", "/services/script/adria_dire",
                                      {"echo": echo, "brigitte": True, "message": texte})
            except Exception as e:
                LOG.warning("Secours voix : %s", str(e)[:100])
        elif not res.get("detail"):
            await self.notifier_tel("🧠 ADRIA a cherché plus loin", texte)

    async def recevoir_action(self, data: dict) -> str | None:
        action = str(data.get("action") or "")
        if not action.startswith("ADRIA_GEN_"):
            return None
        _, _, ident, choix = action.split("_", 3)
        att = self._lire("general_attentes", {})
        a = att.pop(ident, None)
        if not a:
            return None
        self._ecrire("general_attentes", att)
        if choix != "OUI":
            return choix
        try:
            await self.faire(a["prop"])
            await self.notifier_tel("✅ C'est fait", libelle_proposition(a["prop"]))
        except Exception as e:
            LOG.warning("Proposition : %s", str(e)[:150])
            await self.notifier_tel("⚠️ Pas fait", f"Erreur de Home Assistant : {str(e)[:120]}")
        return choix

    async def faire(self, p: dict) -> None:
        if p["type"] == "courses":
            for a in p["articles"]:
                await self.core.appel("POST", "/services/todo/add_item", {"entity_id": "todo.liste_dachats", "item": a})
        elif p["type"] == "agenda":
            await self.core.appel("POST", "/services/calendar/create_event", {
                "entity_id": self.core.courriers.calendrier, "summary": p["titre"],
                "start_date_time": p["debut"] + ":00", "end_date_time": p["fin"] + ":00",
                "description": "Ajouté par ADRIA après ton OUI."})
        elif p["type"] == "rappel":
            r = self._lire("general_rappels", [])
            r.append({"quand": p["quand"], "texte": p["texte"], "fait": False})
            self._ecrire("general_rappels", r[-50:])
        else:
            await self.core.appel("POST", "/services/script/adria_action_voix", {
                "commande": f"memo {p['pour']} {p['texte']}", "echo": "", "perso": "mael"})

    async def minute(self, maintenant: datetime | None = None) -> int:
        """Rappels validés arrivés à échéance (appelé chaque minute)."""
        m = (maintenant or datetime.now()).isoformat(timespec="minutes")
        r = self._lire("general_rappels", [])
        dus = [x for x in r if not x.get("fait") and x["quand"] <= m]
        for x in dus:
            x["fait"] = True
            await self.notifier_tel("⏰ Rappel ADRIA", x["texte"], tag="adria_gen_rappel")
        if dus:
            self._ecrire("general_rappels", r)
        return len(dus)
