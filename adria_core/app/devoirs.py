"""Aide aux devoirs (lot N1).

Un enfant (Aelia, Iwan, Ryan) parle à ADRIA de ses devoirs (« aide-moi pour mes devoirs », « comment on
écrit… », « combien font… ») -> événement `adria_core_devoirs` {question, qui, req_id}.
- ADRIA guide sans donner la réponse : une étape à la fois, une question simple, des encouragements ;
  si l'enfant propose une réponse, elle dit si c'est juste, sinon elle donne un indice.
- Niveau adapté (Aelia CP, Iwan collège, Ryan lycée pro) ; séance suivie 20 minutes (les questions qui
  suivent restent dans la séance, publiée dans sensor.adria_core_devoirs → sessions).
- Les parents reçoivent un petit bilan à 19h15 (qui, combien de questions, sur quoi).
ADRIA ne surveille pas les enfants : elle aide et informe, un adulte reste responsable.
Actif si input_boolean.adria_core_devoirs est ON.
"""

import json
import logging
import re
from datetime import datetime, timedelta

LOG = logging.getLogger("adria_core.devoirs")

INTERRUPTEUR = "input_boolean.adria_core_devoirs"
CAPTEUR = "sensor.adria_core_devoirs"
SEANCE_MIN = 20
NIVEAUX = {
    "aelia": "en CP (6 ans) : apprend a lire (sons, syllabes), compte jusqu a 100, additions simples",
    "iwan": "au college",
    "ryan": "au lycee professionnel (CAP Accompagnant educatif / grand age)",
    "mael": "adulte qui aide son enfant", "vanessa": "adulte qui aide son enfant",
}
PROMPT = (
    "Tu es ADRIA, l aide aux devoirs bienveillante de la famille Kerherve. Tu parles A VOIX HAUTE a {prenom}, "
    "{niveau}. REGLE ABSOLUE : tu ne donnes JAMAIS la reponse finale d un exercice ni le devoir tout fait. "
    "Tu guides une seule etape a la fois : reformule simplement, donne une piste ou un exemple different, puis "
    "pose UNE question courte a l enfant. Si l enfant propose une reponse, dis si c est juste (bravo precis) ; "
    "si c est faux, encourage et donne un indice, sans la solution. Si on te demande de faire le devoir a sa "
    "place, refuse gentiment et propose d avancer ensemble. Pour une lecon a apprendre, aide a la memoriser "
    "(questions, moyen mnemotechnique). Pour Aelia : mots tres simples, syllabes, exemples avec des objets. "
    "Si l enfant parle d un probleme qui l inquiete ou d un danger, dis-lui gentiment d en parler tout de "
    "suite a un adulte de la maison. 3 phrases maximum, francais parle naturel, sans symbole, sans liste, "
    "ecris les nombres et operations en mots (trois plus quatre). {historique}QUESTION DE {prenom} : {question}"
)


def prenom(qui: str) -> str:
    return str(qui or "").split(" ")[0].lower().replace("ë", "e").replace("é", "e")


def sujet(question: str) -> str:
    t = re.sub(r"\s+", " ", str(question or "")).strip()
    return t if len(t) <= 60 else t[:59].rstrip() + "…"


class AgentDevoirs:
    def __init__(self, core, notifier: str) -> None:
        self.core = core
        self.notifier = notifier

    def _lire(self, cle, defaut):
        try:
            return json.loads(self.core.memoire.meta(cle) or "null") or defaut
        except ValueError:
            return defaut

    def _ecrire(self, cle, valeur) -> None:
        self.core.memoire.meta(cle, json.dumps(valeur, ensure_ascii=False))

    def sessions(self, maintenant: datetime | None = None) -> dict[str, str]:
        m = maintenant or datetime.now()
        s = self._lire("devoirs_sessions", {})
        return {p: v["jusqu_a"] for p, v in s.items() if v.get("jusqu_a", "") > m.isoformat(timespec="seconds")}

    async def publier(self) -> None:
        jour = self._lire("devoirs_jour", {})
        try:
            await self.core.appel("POST", f"/states/{CAPTEUR}", {
                "state": len(self.sessions()),
                "attributes": {"friendly_name": "ADRIA — aide aux devoirs (séances en cours)",
                               "icon": "mdi:school", "sessions": self.sessions(),
                               "aujourdhui": {p: len(v) for p, v in jour.get("par", {}).items()}
                               if jour.get("date") == datetime.now().date().isoformat() else {}}})
        except Exception as e:
            LOG.warning("Etat devoirs non publié : %s", str(e)[:100])

    async def aider(self, data: dict, maintenant: datetime | None = None) -> dict:
        m = maintenant or datetime.now()
        res = {"req_id": data.get("req_id", ""), "ok": False, "texte": ""}
        if await self.core.etat(INTERRUPTEUR) != "on":
            res["texte"] = "L'aide aux devoirs est éteinte pour le moment."
            return res
        p = prenom(data.get("qui"))
        if p not in NIVEAUX:
            res["texte"] = "Je n'aide aux devoirs que les membres de la famille."
            return res
        q = str(data.get("question") or "").strip()[:500]
        sessions = self._lire("devoirs_sessions", {})
        s = sessions.get(p) if sessions.get(p, {}).get("jusqu_a", "") > m.isoformat(timespec="seconds") else None
        histo = (s or {}).get("echanges", [])[-3:]
        historique = ("DEBUT DE LA SEANCE (questions et reponses precedentes) : "
                      + " | ".join(f"Q: {e['q']} R: {e['r']}" for e in histo) + ". ") if histo else ""
        rep = await self.core.routeur.demander(
            PROMPT.format(prenom=p.capitalize(), niveau=NIVEAUX[p], historique=historique, question=q),
            "interactif", "adria_core_devoirs")
        if not rep.get("ok"):
            res["texte"] = "Mes IA ne répondent pas pour l'instant. Réessaie dans une minute."
            return res
        texte = re.sub(r"[*#_`]", "", str(rep.get("data") or "")).strip()[:700]
        histo = histo + [{"q": q[:200], "r": texte[:250]}]
        sessions[p] = {"jusqu_a": (m + timedelta(minutes=SEANCE_MIN)).isoformat(timespec="seconds"),
                       "echanges": histo[-4:]}
        self._ecrire("devoirs_sessions", sessions)
        jour = self._lire("devoirs_jour", {})
        if jour.get("date") != m.date().isoformat():
            jour = {"date": m.date().isoformat(), "par": {}}
        jour["par"].setdefault(p, []).append(sujet(q))
        self._ecrire("devoirs_jour", jour)
        if not s:
            self.core.memoire.ajouter("devoirs", [{"summary": f"{p.capitalize()} : aide aux devoirs ({sujet(q)})",
                                                   "date": m.date().isoformat()}])
        await self.publier()
        res.update(ok=True, texte=texte)
        return res

    async def bilan_soir(self) -> None:
        if await self.core.etat(INTERRUPTEUR) != "on":
            return
        jour = self._lire("devoirs_jour", {})
        if jour.get("date") != datetime.now().date().isoformat() or not jour.get("par"):
            return
        lignes = [f"• {p.capitalize()} : {len(v)} question(s) — " + " ; ".join(v[:3]) + ("…" if len(v) > 3 else "")
                  for p, v in jour["par"].items()]
        domaine, service = self.notifier.split(".", 1)
        await self.core.appel("POST", f"/services/{domaine}/{service}", {
            "title": "📚 Devoirs avec ADRIA aujourd'hui", "message": "\n".join(lignes),
            "data": {"tag": "adria_devoirs", "channel": "ADRIA famille"}})
