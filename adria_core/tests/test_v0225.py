"""v0.22.5 — chaîne « brain » : Gemini Lite d'abord, voix et autres chaînes inchangées."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import routeur as R  # noqa: E402
from test_ascension import faire  # noqa: E402


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def test_chaine_brain():
    assert R.CHAINES["brain"] == [("gemini_lite", 10), ("gemini", 12), ("claude", 15)]
    assert R.ordonner(R.CHAINES["brain"], True, "brain") == R.CHAINES["brain"]


def test_autres_chaines_inchangees():
    assert R.CHAINES["interactif"] == [("gemini", 12), ("gemini_lite", 8), ("claude", 15)]
    assert R.CHAINES["rapide"][0] == ("gemini", 60) and R.CHAINES["profond"][0] == ("claude", 60)


class HA:
    def __init__(self):
        self.appels = []

    async def etat(self, e):
        return {"input_boolean.adria_claude_actif": "off"}.get(e, "0")

    async def ai_task(self, entite, prompt, tache):
        self.appels.append(entite)
        return "réponse"

    async def ollama(self, p):
        return ""

    async def statut(self, t):
        pass

    async def incrementer(self, c):
        pass

    async def noter(self, *a):
        pass


def test_routeur_brain_appelle_lite_en_premier():
    ha = HA()
    r = run(R.Routeur(ha).demander("q", "brain", "t"))
    assert r["ok"] and r["moteur"] == "gemini_lite" and ha.appels == [R.ENTITES["gemini_lite"]]


def test_brain_utilise_la_chaine_brain(tmp_path):
    class Rt:
        prof = None

        async def demander(self, prompt, profondeur="rapide", tache="adria", **k):
            Rt.prof = profondeur
            return {"ok": True, "data": '{"action": "repondre", "type": "general", "reponse": "391"}',
                    "moteur": "gemini_lite"}
    c = faire(tmp_path)
    c.routeur = Rt()
    run(c.brain.repondre({"question": "17 fois 23", "qui": "Mael"}))
    assert Rt.prof == "brain"
    run(c.brain.repondre({"question": "17 fois 23", "qui": "Mael", "voix": "true"}))
    assert Rt.prof == "brain"
