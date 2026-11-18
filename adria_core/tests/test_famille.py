import asyncio
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import competences as K  # noqa: E402
import journal as J  # noqa: E402
from store import Memoire  # noqa: E402

DIM = date(2026, 11, 22)


# ------------------------------------------------------------------ J1 journal
def test_texte_souvenir():
    assert J.texte_souvenir("ADRIA, garde ce souvenir : Aelia a eu sa première médaille") == \
        "Aelia a eu sa première médaille"
    assert J.texte_souvenir("garde comme souvenir que Ryan a arbitré son 1er match") .startswith("Que Ryan")
    assert J.texte_souvenir("garde ce souvenir") == ""


def test_candidats():
    lignes = [{"date": "2026-11-15", "resume": "15/11/2026 11:00 Patin Aelia (rue de Jersey)"},
              {"date": "2026-11-15", "resume": "15/11/2026 11:00 Patin Aelia (rue de Jersey)"},
              {"date": "2026-11-17", "resume": "17/11/2026 08:00 Cours de maths"},
              {"date": "2026-11-18", "resume": "18/11/2026 Match arbitrage Ryan"},
              {"date": "2026-11-19", "resume": "19/11/2026 Dentiste Iwan"},
              {"date": "2026-11-20", "resume": "20/11/2026 Anniversaire mamie"},
              {"date": "2026-11-21", "resume": "21/11/2026 Restaurant en famille"}]
    c = J.candidats(lignes, {}, set(), DIM)
    assert [x["texte"] for x in c] == ["Patin Aelia", "Match arbitrage Ryan", "Anniversaire mamie"]
    refus = {c[0]["cle"]: "2026-11-01"}  # patin refusé il y a 3 semaines : pas reproposé
    assert "Patin Aelia" not in [x["texte"] for x in J.candidats(lignes, refus, set(), DIM)]
    assert "Patin Aelia" in [x["texte"] for x in J.candidats(lignes, {c[0]["cle"]: "2026-08-01"}, set(), DIM)]


class FauxCore:
    def __init__(self, tmp):
        self.memoire = Memoire(str(tmp / "b.db"))
        self.notifs = []
        self.reponse_ia = {"ok": True, "data": "Une belle année ensemble."}
        self._etat = "on"
        outer = self

        class R:
            async def demander(self, *a, **k):
                outer.prompt = a[0]
                return outer.reponse_ia
        self.routeur = R()

    async def etat(self, e):
        return self._etat

    async def appel(self, methode, chemin, data=None, delai=60):
        if chemin.startswith("/services/notify"):
            self.notifs.append(data)
        if chemin == "/states":
            return self.etats
        self.dernier_appel = (chemin, data)
        return {}


def test_journal_bout_en_bout(tmp_path, monkeypatch):
    import classement
    monkeypatch.setattr(classement, "RACINE", str(tmp_path / "docs"))
    core = FauxCore(tmp_path)
    core.memoire.ajouter("agenda", [{"summary": "21/11/2026 11:00 Patin Aelia", "date": "2026-11-21"},
                                    {"summary": "20/11/2026 Match arbitrage Ryan", "date": "2026-11-20"}])
    j = J.AgentJournal(core, "notify.tel")
    assert asyncio.run(j.proposer(DIM))["proposes"] == 2
    acts = [n["data"]["actions"][0]["action"] for n in core.notifs]
    assert j.recevoir_action({"action": acts[0]}) == "OUI"
    assert j.recevoir_action({"action": acts[1].replace("_OUI", "_NON")}) == "NON"
    assert j.recevoir_action({"action": acts[0]}) is None  # déjà répondu
    r = asyncio.run(j.garder_voix({"texte": "garde ce souvenir : Iwan a fini son puzzle", "qui": "Mael"}))
    assert r["ok"] and len(j.souvenirs()) == 2
    assert asyncio.run(j.proposer(DIM))["proposes"] == 0  # gardé + refusé : rien de nouveau
    assert core.memoire.chercher("souvenir puzzle", 3)
    livre = asyncio.run(j.livre(2026))
    contenu = (tmp_path / "docs" / livre["fichier"]).read_text()
    assert "Novembre" in contenu and "Match arbitrage Ryan" in contenu and "Une belle année" in contenu


# ------------------------------------------------------------------ C1 compétences
ETATS = [{"entity_id": "sensor.lave_linge_etat", "attributes": {"friendly_name": "Lave-linge état"}},
         {"entity_id": "switch.prise_lave_linge", "attributes": {"friendly_name": "Prise lave-linge"}},
         {"entity_id": "alarm_control_panel.maison", "attributes": {"friendly_name": "Alarme lave-linge"}},
         {"entity_id": "person.mael_kerherve", "attributes": {"friendly_name": "Mael"}},
         {"entity_id": "input_select.quart_mael", "attributes": {}}]
AUTO = {"alias": "Lave-linge fini", "mode": "single",
        "triggers": [{"trigger": "state", "entity_id": "sensor.lave_linge_etat", "to": "fini"}],
        "conditions": [],
        "actions": [{"action": "notify.mobile_app_telephone_mael_sm_s921b",
                     "data": {"title": "Lave-linge", "message": "Il a fini"}}]}


def test_demande_et_entites():
    assert K.demande_propre("ADRIA, apprends à me prévenir quand le lave-linge a fini.") == \
        "me prévenir quand le lave-linge a fini"
    ents = [e for e, _ in K.choisir_entites("me prévenir quand le lave-linge a fini", ETATS)]
    assert "sensor.lave_linge_etat" in ents and "alarm_control_panel.maison" not in ents


def test_verifier():
    connues = {e["entity_id"] for e in ETATS}
    assert K.verifier(AUTO, connues) == []
    mauvais = json.loads(json.dumps(AUTO))
    mauvais["actions"].append({"action": "lock.unlock", "target": {"entity_id": "lock.porte"}})
    assert K.verifier(mauvais, connues)
    cache = json.loads(json.dumps(AUTO))
    cache["actions"] = [{"if": [], "then": [{"action": "homeassistant.restart"}]}]
    assert K.verifier(cache, connues)
    inconnue = json.loads(json.dumps(AUTO))
    inconnue["triggers"][0]["entity_id"] = "sensor.invente"
    assert "entité inconnue : sensor.invente" in K.verifier(inconnue, connues)
    assert K.verifier(dict(AUTO, actions=[{"action": "script.turn_on"}]), connues)
    assert K.verifier(dict(AUTO, actions=[{"action": "script.adria_dire",
                                           "data": {"echo": "media_player.iwan"}}]), connues | {"media_player.iwan"}) == []


def test_competence_bout_en_bout(tmp_path):
    core = FauxCore(tmp_path)
    core.etats = ETATS
    core.reponse_ia = {"ok": True, "data": json.dumps({"possible": True, "explication": "Je te préviens.",
                                                       "automatisation": AUTO})}
    cree = []

    async def ws(msg):
        cree.append(msg)
        return [{"entity_id": "automation.adria_appris_x", "unique_id": "x"}]
    core.ws_commande = ws
    c = K.AgentCompetences(core, "notify.tel")
    r = asyncio.run(c.apprendre({"demande": "apprends à me prévenir quand le lave-linge a fini"}))
    assert r["ok"] and "lave-linge" in core.prompt and "alarm_control_panel" not in core.prompt
    act = core.notifs[-1]["data"]["actions"][0]["action"]
    assert asyncio.run(c.recevoir_action({"action": act})) == "OUI"
    assert c.dernier["etat"] == "créée" and c._lire("comp_creees", [])[0]["id"].startswith("adria_appris_")
    # refusée par le contrôle
    core.reponse_ia = {"ok": True, "data": json.dumps({"possible": True, "automatisation": dict(
        AUTO, actions=[{"action": "alarm_control_panel.alarm_disarm"}])})}
    r = asyncio.run(c.apprendre({"demande": "apprends à couper l'alarme quand je rentre"}))
    assert not r["ok"] and "permis" in r["texte"]
    core._etat = "off"
    assert not asyncio.run(c.apprendre({"demande": "apprends à me prévenir de la pluie"}))["ok"]
