"""v0.22.4 — diagnostic des missions BRAIN (durées par étape), état du Core lu d'office."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import brain as BR  # noqa: E402
from test_ascension import faire  # noqa: E402
from test_v0222 import RouteurLent  # noqa: E402


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def test_question_technique_toutes_ia_en_panne(tmp_path):
    """Cas réel du 25/11 19h59 : aucune réponse d'IA -> la version est quand même donnée, sans IA."""
    c = faire(tmp_path, [None])
    r = run(c.brain.repondre({"question": "Quelle version d'ADRIA Core tourne en ce moment ?", "qui": "Mael"}))
    assert r["texte"].startswith("Voici ce que j'ai vérifié : ADRIA Core 0.22.0") and r["statut"] == "sait"
    assert r["outils"] == ["memoire_chercher", "etat_core"]
    noms = [e["etape"] for e in r["etapes"]]
    assert noms[:3] == ["memoire", "etat_core_d_office", "ia"] and r["etapes"][2]["ok"] is False


def test_question_non_technique_pas_d_etat_core(tmp_path):
    c = faire(tmp_path, [None])
    r = run(c.brain.repondre({"question": "Quand ai-je changé les pneus du 5008 ?", "qui": "Mael"}))
    assert "etat_core" not in r["outils"]


def test_enfant_pas_d_etat_core(tmp_path):
    c = faire(tmp_path, [None])
    r = run(c.brain.repondre({"question": "Quelle version d'ADRIA Core ?", "qui": "Iwan"}))
    assert "etat_core" not in r["outils"]
    assert {"etape": "etat_core_d_office", "autorise": False} == {k: v for k, v in r["etapes"][1].items() if k != "s"}


def test_pas_de_double_appel(tmp_path):
    c = faire(tmp_path, [{"action": "outil", "outil": "etat_core", "args": {}},
                         {"action": "repondre", "type": "maison", "reponse": "Version 0.22.0.", "preuves": ["O1"]}])
    r = run(c.brain.repondre({"question": "Quelle version tourne ?", "qui": "Mael"}))
    assert r["outils"].count("etat_core") == 1 and "déjà consulté" in c.routeur.prompts[1]


def test_etapes_ia_avec_moteur_et_delai(tmp_path, monkeypatch):
    monkeypatch.setattr(BR, "DELAI_TEXTE", 1.0)
    monkeypatch.setattr(BR, "MARGE_MIN", 0.2)
    c = faire(tmp_path)
    c.routeur = RouteurLent(5)
    r = run(c.brain.repondre({"question": "gâteau chocolat Iwan", "qui": "Mael"}))
    ia = [e for e in r["etapes"] if e["etape"] == "ia"]
    assert ia and ia[0]["ok"] is False and "délai de la mission atteint" in ia[0]["raison"]
    assert all(isinstance(e["s"], float) for e in r["etapes"])


def test_etapes_publiees_et_journalisees(tmp_path, caplog):
    c = faire(tmp_path, [{"action": "repondre", "type": "general", "reponse": "391", "preuves": []}])
    with caplog.at_level("INFO", logger="adria_core.brain"):
        run(c.brain.repondre({"question": "17 fois 23", "qui": "Mael"}))
    m = c.brain.etat()["attributes"]["derniere_mission"]
    assert [e["etape"] for e in m["etapes"]] == ["memoire", "ia"] and m["etapes"][1]["moteur"] == "gemini"
    assert json.dumps(m) and any("BRAIN" in x.message and "memoire" in x.message for x in caplog.records)
