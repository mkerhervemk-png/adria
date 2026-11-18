"""Compétences apprises à la demande (lot C1).

Mael dit « ADRIA, apprends à me prévenir quand le lave-linge a fini » (événement `adria_core_apprendre`
{demande, qui, req_id}, envoyé par avatar_demander, Mael seulement).
1. Le Core cherche les entités qui correspondent aux mots de la demande (0 IA).
2. Un appel IA (profondeur « profond ») propose UNE automatisation Home Assistant en JSON + 2 lignes
   d'explication.
3. Contrôle 0 IA : actions et entités autorisées seulement (liste blanche ci-dessous) ; jamais
   alarme, sirène, serrure, caméra, surveillance, internet, redémarrage, scripts ou autres automatisations.
4. Notification à Mael : explication + OUI / NON. Rien n'est créé sans OUI.
5. OUI -> automatisation « ADRIA appris — … » créée (id adria_appris_…), étiquette agent_appris.
   Événement `adria_core_competences_couper` -> toutes les compétences apprises sont désactivées
   (jamais supprimées).
Actif si input_boolean.adria_core_competences est ON.
"""

import json
import logging
import re
import uuid
from datetime import datetime

from store import mots_cles, normaliser

LOG = logging.getLogger("adria_core.competences")

INTERRUPTEUR = "input_boolean.adria_core_competences"
ETIQUETTE = "agent_appris"
PREFIXE_ID = "adria_appris_"
MAX_ENTITES = 40
DOMAINES_CONTEXTE = ("sensor.", "binary_sensor.", "light.", "switch.", "media_player.", "person.",
                     "input_boolean.", "input_select.", "calendar.", "weather.", "sun.", "cover.", "climate.",
                     "vacuum.", "device_tracker.", "todo.", "counter.", "timer.")
ACTIONS_OK = {
    "light.turn_on", "light.turn_off", "light.toggle", "switch.turn_on", "switch.turn_off",
    "media_player.media_pause", "media_player.media_play", "media_player.volume_set",
    "media_player.turn_off", "persistent_notification.create", "todo.add_item", "counter.increment",
    "input_boolean.turn_on", "input_boolean.turn_off", "climate.set_temperature",
    "cover.open_cover", "cover.close_cover", "cover.set_cover_position",
    "vacuum.start", "vacuum.return_to_base", "script.adria_dire",
}
NOTIFIER_OK = re.compile(r"^notify\.mobile_app_[a-z0-9_]+$")
INTERDIT = re.compile(r"alarm|alarme|siren|sirene|lock|serrure|verrou|camera|cam_|tapo|privacy|privee|"
                      r"surveillance|securite|internet|wifi|wi_fi|routeur|livebox|frigate|garage|portail|"
                      r"adria_core|adria_appris|ia_gemini|homeassistant|hassio|supervisor|shell|"
                      r"python_script|rest_command|automation\.|script\.(?!adria_dire)", re.I)
CLES_OK = {"alias", "description", "mode", "triggers", "conditions", "actions", "variables",
           "max", "max_exceeded"}
PROMPT = (
    "Tu es l'assistant qui ecrit des automatisations Home Assistant (format 2024+ : cles triggers, "
    "conditions, actions ; chaque trigger a 'trigger:', chaque action a 'action:'). Demande de Mael : "
    "« {demande} ». Entites disponibles (entity_id = nom) :\n{entites}\n"
    "Notifications : action notify.mobile_app_telephone_mael_sm_s921b avec data.title et data.message. "
    "Parler sur une Echo : action script.adria_dire avec data.echo (media_player de l'Echo) et data.message. "
    "Actions autorisees SEULEMENT : notify.mobile_app_*, " + ", ".join(sorted(ACTIONS_OK)) + ", delay, "
    "wait_for_trigger, choose, if. Interdit : alarme, sirene, serrures, cameras, surveillance, internet, "
    "redemarrage, autres scripts ou automatisations. N utilise QUE des entites de la liste. "
    "Reponds UNIQUEMENT par un JSON : {{\"possible\": true ou false, \"pourquoi\": \"si impossible, en une "
    "phrase\", \"explication\": \"ce que fera l automatisation, 2 phrases simples pour Mael\", "
    "\"automatisation\": {{\"alias\": \"titre court\", \"mode\": \"single\", \"triggers\": [...], "
    "\"conditions\": [...], \"actions\": [...]}}}}"
)
PHRASE = re.compile(r"^.*?\bapprends?[- ](?:a|à)\s+", re.I)


# ------------------------------------------------------------------ outils purs (testés)

def demande_propre(phrase: str) -> str:
    t = PHRASE.sub("", str(phrase or "").strip(), count=1).strip(" .?!")
    return t[:300]


def choisir_entites(demande: str, etats: list[dict], n: int = MAX_ENTITES) -> list[tuple[str, str]]:
    """Entités dont l'id ou le nom contient un mot de la demande (sans les entités interdites)."""
    mots = mots_cles(demande)
    notes = []
    for e in etats:
        eid = e.get("entity_id", "")
        if not eid.startswith(DOMAINES_CONTEXTE) or INTERDIT.search(eid):
            continue
        nom = str((e.get("attributes") or {}).get("friendly_name") or eid)
        jetons = set(re.findall(r"[a-z0-9]+", normaliser(f"{eid} {nom}")))
        score = sum(1 for m in mots if any(j.startswith(m) for j in jetons))
        if score:
            notes.append((score, eid, nom[:60]))
    notes.sort(key=lambda t: (-t[0], t[1]))
    fixes = [("person.mael_kerherve", "Mael"), ("input_select.quart_mael", "quart de Mael"),
             ("sun.sun", "soleil")]
    res = [(eid, nom) for _, eid, nom in notes[:n]]
    return res + [f for f in fixes if f[0] not in {r[0] for r in res}]


def _actions(bloc) -> list[dict]:
    """Toutes les actions, y compris dans choose / if / repeat / sequence."""
    out = []
    for a in bloc if isinstance(bloc, list) else [bloc]:
        if not isinstance(a, dict):
            continue
        out.append(a)
        for k in ("then", "else", "sequence", "default"):
            if k in a:
                out += _actions(a[k])
        for c in a.get("choose") or []:
            if isinstance(c, dict):
                out += _actions(c.get("sequence") or [])
        if isinstance(a.get("repeat"), dict):
            out += _actions(a["repeat"].get("sequence") or [])
        if isinstance(a.get("parallel"), list):
            out += _actions(a["parallel"])
    return out


def verifier(auto, connues: set[str]) -> list[str]:
    """Liste des problèmes (vide = autorisé)."""
    if not isinstance(auto, dict):
        return ["pas d'automatisation"]
    pb = []
    if set(auto) - CLES_OK:
        pb.append(f"clés inattendues : {', '.join(sorted(set(auto) - CLES_OK))}")
    if not auto.get("triggers") or not auto.get("actions"):
        pb.append("déclencheur ou action manquant")
    texte = json.dumps(auto, ensure_ascii=False)
    m = INTERDIT.search(texte)
    if m:
        pb.append(f"élément interdit : {m.group(0)}")
    for a in _actions(auto.get("actions") or []):
        act = a.get("action") or a.get("service")
        if act and act not in ACTIONS_OK and not NOTIFIER_OK.match(str(act)):
            pb.append(f"action non autorisée : {act}")
        if a.get("event"):
            pb.append("envoi d'événement non autorisé")
    for eid in set(re.findall(r"\b(?:sensor|binary_sensor|light|switch|media_player|person|input_boolean|"
                              r"input_select|calendar|weather|cover|climate|vacuum|device_tracker|todo|"
                              r"counter|timer)\.[a-z0-9_]+", texte)):
        if eid not in connues:
            pb.append(f"entité inconnue : {eid}")
    return pb


def finaliser(auto: dict, demande: str, ident: str) -> dict:
    a = {k: v for k, v in auto.items() if k in CLES_OK}
    a["alias"] = "ADRIA appris — " + str(auto.get("alias") or demande)[:60]
    a["description"] = (f"Compétence apprise à la demande de Mael le {datetime.now().strftime('%d/%m/%Y')} : "
                        f"« {demande[:200]} ». Créée par ADRIA Core après son OUI (lot C1).")
    a.setdefault("mode", "single")
    a.setdefault("conditions", [])
    a["id"] = PREFIXE_ID + ident
    return a


# --------------------------------------------------------------------------- l'agent

class AgentCompetences:
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

    async def notifier_tel(self, titre, message, actions=None, tag="adria_competence") -> None:
        domaine, service = self.notifier.split(".", 1)
        data = {"tag": tag, "channel": "ADRIA famille", "sticky": "true"}
        if actions:
            data["actions"] = actions
        await self.core.appel("POST", f"/services/{domaine}/{service}",
                              {"title": titre, "message": message, "data": data})

    async def apprendre(self, data: dict) -> dict:
        import formulaires as F
        res = {"req_id": data.get("req_id", ""), "ok": False, "texte": ""}
        if await self.core.etat(INTERRUPTEUR) != "on":
            res["texte"] = "L'apprentissage de compétences est éteint."
            return res
        demande = demande_propre(data.get("demande") or "")
        if len(demande) < 6:
            res["texte"] = "Dis-moi quoi apprendre, par exemple : apprends à me prévenir quand le lave-linge a fini."
            return res
        etats = await self.core.appel("GET", "/states")
        entites = choisir_entites(demande, etats)
        connues = {e["entity_id"] for e in etats}
        rep = await self.core.routeur.demander(
            PROMPT.format(demande=demande, entites="\n".join(f"{e} = {n}" for e, n in entites)),
            "profond", "adria_core_competence")
        if not rep.get("ok"):
            res["texte"] = "Je n'ai pas réussi à préparer cette compétence (IA indisponible). Réessaie plus tard."
            return res
        d = F.lire_json(rep.get("data") or "")
        if not d.get("possible", True) or not isinstance(d.get("automatisation"), dict):
            res["texte"] = "Je ne peux pas apprendre ça : " + str(d.get("pourquoi") or "il me manque un capteur.")[:200]
            return res
        pbs = verifier(d["automatisation"], connues)
        if pbs:
            LOG.info("Compétence refusée : %s", pbs)
            res["texte"] = ("Je ne peux pas apprendre ça sans sortir de ce qui m'est permis ("
                            + pbs[0] + "). Demande-le autrement ou fais-le valider par Claude.")
            return res
        ident = uuid.uuid4().hex[:8]
        auto = finaliser(d["automatisation"], demande, ident)
        attentes = self._lire("comp_attentes", {})
        attentes[ident] = {"auto": auto, "demande": demande, "explication": str(d.get("explication") or "")[:300]}
        self._ecrire("comp_attentes", attentes)
        await self.notifier_tel(
            "🧠 Nouvelle compétence à valider",
            f"« {demande} »\n{attentes[ident]['explication']}\nJe la crée ?",
            [{"action": f"ADRIA_COMP_{ident}_OUI", "title": "OUI, crée-la"},
             {"action": f"ADRIA_COMP_{ident}_NON", "title": "NON"}], tag=f"adria_competence_{ident}")
        res.update(ok=True, texte="J'ai préparé cette compétence : je t'ai envoyé l'explication sur ton téléphone. "
                                  "Elle ne sera créée que si tu dis OUI.")
        self.dernier = {"demande": demande, "etat": "en attente"}
        return res

    async def recevoir_action(self, data: dict) -> str | None:
        action = str(data.get("action") or "")
        if not action.startswith("ADRIA_COMP_"):
            return None
        _, _, ident, choix = action.split("_", 3)
        attentes = self._lire("comp_attentes", {})
        p = attentes.pop(ident, None)
        if not p:
            return None
        self._ecrire("comp_attentes", attentes)
        if choix != "OUI":
            self.dernier = {"demande": p["demande"], "etat": "refusée"}
            return choix
        try:
            await self.creer(p["auto"])
            creees = self._lire("comp_creees", [])
            creees.append({"id": p["auto"]["id"], "alias": p["auto"]["alias"],
                           "le": datetime.now().isoformat(timespec="seconds")})
            self._ecrire("comp_creees", creees)
            self.core.memoire.ajouter("competences", [{
                "summary": f"Compétence apprise : {p['demande']} ({p['explication']})",
                "date": datetime.now().date().isoformat()}])
            await self.notifier_tel("✅ Compétence créée", f"{p['auto']['alias']}\nTu peux la couper dans "
                                    "Automatisations (étiquette « Appris à la demande »).")
            self.dernier = {"demande": p["demande"], "etat": "créée"}
        except Exception as e:
            LOG.warning("Création compétence : %s", str(e)[:150])
            await self.notifier_tel("⚠️ Compétence non créée", f"Erreur de Home Assistant : {str(e)[:120]}")
            self.dernier = {"demande": p["demande"], "etat": "erreur"}
        return choix

    async def creer(self, auto: dict) -> None:
        await self.core.appel("POST", f"/config/automation/config/{auto['id']}", auto)
        try:  # l'étiquette passe par le registre (WebSocket) ; sans elle la compétence marche quand même
            ent = await self.core.ws_commande({"type": "config/entity_registry/list"})
            eid = next((e["entity_id"] for e in ent or [] if e.get("unique_id") == auto["id"]), None)
            if eid:
                await self.core.ws_commande({"type": "config/entity_registry/update", "entity_id": eid,
                                             "labels": [ETIQUETTE]})
        except Exception as e:
            LOG.warning("Étiquette compétence : %s", str(e)[:120])

    async def couper_tout(self) -> int:
        n = 0
        etats = await self.core.appel("GET", "/states")
        ids = {c["id"] for c in self._lire("comp_creees", [])}
        for e in etats:
            if e["entity_id"].startswith("automation.") and (e.get("attributes") or {}).get("id") in ids:
                await self.core.appel("POST", "/services/automation/turn_off", {"entity_id": e["entity_id"]})
                n += 1
        await self.notifier_tel("🧠 Compétences coupées", f"{n} compétence(s) apprise(s) désactivée(s), rien n'est supprimé.")
        return n
