import asyncio
import glob
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from memoire_agent import AgentMemoire, construire_prompt, lire_reponse, presences_du_jour  # noqa: E402
from store import Memoire  # noqa: E402


def test_aucune_suppression_dans_le_code_du_core():
    app = os.path.join(os.path.dirname(__file__), "..", "app")
    for f in glob.glob(os.path.join(app, "*.py")):
        code = open(f, encoding="utf-8").read()
        assert not re.search(r"\b(DELETE\s+FROM|DROP\s+TABLE|TRUNCATE)\b", code, re.I), f


def test_lire_reponse():
    c, r = lire_reponse("**CARNET:** Journée calme.\nRéflexion : Reussi : x | A ameliorer : y | Demain je : z")
    assert c == "Journée calme." and r.startswith("Reussi")
    assert lire_reponse("Juste une ligne")[0] == "Juste une ligne"


def test_presences():
    h = [{"texte": "17/11 07:10 Ryan : absent", "detail": "person.ryan"},
         {"texte": "17/11 08:00 Lampe : allumé", "detail": "light.salon"},
         {"texte": "17/11 20:00 TV : allumé", "detail": "media_player.lg"}]
    t = presences_du_jour(h)
    assert "07:10 Ryan : absent" in t and "Lampe" not in t and "1 changements" in t


def test_prompt_contient_presences():
    p = construire_prompt(date(2026, 11, 17), {"presences": "07:10 Ryan : absent"})
    assert "PRESENCES_REELLES" in p and "07:10 Ryan" in p and "mar. 17/11/2026" in p


class FauxRouteur:
    async def demander(self, prompt, prof, tache):
        return {"ok": True, "moteur": "gemini",
                "data": "CARNET: Ryan parti à 07h10, Tempo bleu.\nREFLEXION: Reussi : a | A ameliorer : b | Demain je : c"}


class FauxCore:
    def __init__(self, etats):
        self.etats, self.appels = etats, []
        self.memoire = Memoire(":memory:")
        self.routeur = FauxRouteur()

    async def etat(self, e):
        return self.etats.get(e, "x")

    async def importer_historique(self):
        self.memoire.ajouter("historique", [{"date": date.today().isoformat(),
                                             "summary": "17/11 07:10 Ryan : absent", "description": "person.ryan"}])
        return 1

    async def appel(self, m, chemin, d=None, delai=60):
        self.appels.append((chemin, d))
        return {"service_response": {}}


def test_carnet_reel_ecrit_et_garde():
    c = FauxCore({"input_boolean.adria_core_memoire": "on", "input_boolean.adria_jarvis_actif": "on",
                  "input_boolean.adria_conscience_actif": "on"})
    r = asyncio.run(AgentMemoire(c).carnet())
    assert r["ok"] and "07h10" in r["carnet"]
    ecrits = [d["entity_id"] for ch, d in c.appels if d and "entity_id" in d and ch.endswith(("add_item", "set_value"))]
    assert ecrits == ["todo.adria_jarvis_episodes", "todo.adria_journal_interieur",
                      "input_text.adria_conscience_derniere_reflexion"]
    assert c.memoire.stats()["par_source"].get("adria_jarvis_episodes") == 1


def test_carnet_essai_n_ecrit_rien_et_off_ne_fait_rien():
    c = FauxCore({"input_boolean.adria_core_memoire": "off"})
    assert not asyncio.run(AgentMemoire(c).carnet())["ok"]
    r = asyncio.run(AgentMemoire(c).carnet(essai=True))
    assert r["ok"] and not any(ch.endswith(("add_item", "set_value")) for ch, _ in c.appels)
