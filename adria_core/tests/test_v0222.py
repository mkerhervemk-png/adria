"""v0.22.2 — délai maximal des missions BRAIN, chaîne courte, contradictions à trancher. Données fictives."""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import brain as BR  # noqa: E402
import main  # noqa: E402
from test_ascension import faire  # noqa: E402


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


class RouteurLent:
    """Chaque appel prend `duree` secondes (Gemini saturé)."""

    def __init__(self, duree, reponse=None):
        self.duree, self.appels = duree, []
        self.reponse = reponse or {"action": "outil", "outil": "etat_core", "args": {}}

    async def demander(self, prompt, profondeur="rapide", tache="adria", **k):
        self.appels.append(profondeur)
        await asyncio.sleep(self.duree)
        return {"ok": True, "data": json.dumps(self.reponse), "moteur": "gemini"}


def test_chaine_courte_en_texte_et_a_la_voix(tmp_path):
    c = faire(tmp_path, [{"action": "repondre", "type": "general", "reponse": "391", "preuves": []}] * 2)
    run(c.brain.repondre({"question": "17 fois 23", "qui": "Mael"}))
    run(c.brain.repondre({"question": "17 fois 23", "qui": "Mael", "voix": "true"}))
    assert all(k == {} for k in c.routeur.appels)  # pas de moteur forcé
    assert "rapide" not in open(os.path.join(os.path.dirname(__file__), "..", "app", "brain.py")).read().split(
        "echeance =")[1].split("def _verifier")[0]


def test_delai_depasse_reponse_sans_nouvel_appel(tmp_path, monkeypatch):
    monkeypatch.setattr(BR, "DELAI_TEXTE", 1.0)
    monkeypatch.setattr(BR, "MARGE_MIN", 0.2)
    c = faire(tmp_path)
    c.routeur = RouteurLent(5)
    t = time.monotonic()
    r = run(c.brain.repondre({"question": "gâteau chocolat Iwan", "qui": "Mael"}))
    assert time.monotonic() - t < 2.0  # coupé au délai, pas 5 s
    assert r["delai_depasse"] and r["ok"] and r["texte"].startswith("Voici ce que j'ai trouvé")
    assert len(c.routeur.appels) == 1


def test_delai_sans_rien_trouve(tmp_path, monkeypatch):
    monkeypatch.setattr(BR, "DELAI_TEXTE", 0.5)
    monkeypatch.setattr(BR, "MARGE_MIN", 0.1)
    c = faire(tmp_path)
    c.routeur = RouteurLent(3)
    r = run(c.brain.repondre({"question": "licorne arc-en-ciel", "qui": "Mael"}))
    assert r["delai_depasse"] and r["texte"] == BR.NE_SAIS_PAS and r["statut"] == "ignore"


def test_pas_de_nouvel_appel_sous_la_marge(tmp_path, monkeypatch):
    monkeypatch.setattr(BR, "DELAI_TEXTE", 1.2)
    monkeypatch.setattr(BR, "MARGE_MIN", 1.0)
    c = faire(tmp_path)
    c.routeur = RouteurLent(0.5)  # 1er appel : outil (0,5 s) ; il reste < 1 s -> aucun 2e appel
    r = run(c.brain.repondre({"question": "Quelle version ?", "qui": "Mael"}))
    assert len(c.routeur.appels) == 1 and r["delai_depasse"] and "etat_core" in r["outils"]


def test_voix_un_seul_tour_si_preuve_fiable(tmp_path):
    # mémoire perso (déclarée, fiabilité 0,85) : un seul tour
    c = faire(tmp_path, [{"action": "repondre", "type": "maison", "reponse": "À 45000 km.", "preuves": ["M1"]}])
    r = run(c.brain.repondre({"question": "pneus 5008 45000", "qui": "Mael", "voix": "true"}))
    assert r["tours"] == 1 and "DERNIER tour" in c.routeur.prompts[0]


def test_voix_garde_deux_tours_si_preuve_faible(tmp_path):
    # souvenir raconté (fiabilité 0,65 < 0,7) : pas de raccourci
    c = faire(tmp_path, [{"action": "repondre", "type": "maison", "reponse": "Au chocolat.", "preuves": ["M1"]}])
    run(c.brain.repondre({"question": "gâteau chocolat Iwan", "qui": "Mael", "voix": "true"}))
    assert "DERNIER tour" not in c.routeur.prompts[0]


def test_texte_garde_ses_tours(tmp_path):
    c = faire(tmp_path, [{"action": "outil", "outil": "etat_core", "args": {}},
                         {"action": "repondre", "type": "maison", "reponse": "Version 0.22.0.", "preuves": ["O1"]}])
    r = run(c.brain.repondre({"question": "gâteau chocolat Iwan version", "qui": "Mael"}))
    assert r["tours"] == 2 and "delai_depasse" not in r


def test_contradictions_publiees_et_tranchees(tmp_path):
    c = faire(tmp_path, [{"action": "repondre", "type": "maison", "reponse": "À 52000 km.", "preuves": ["M1", "M2"]}])
    run(c.brain.repondre({"question": "pneus 5008 km", "qui": "Mael"}))
    a = c.brain.etat()["attributes"]
    assert a["contradictions_ouvertes"] == 1 and len(a["contradictions"]) == 1
    cid = a["contradictions"][0]["id"]
    assert set(a["contradictions"][0]) == {"id", "quand", "motif", "a", "b"}
    assert c.brain.trancher({"ids": ["x", 999]})["tranchees"] == 0
    assert c.brain.trancher({"ids": [cid]}) == {"req_id": "", "ok": True, "tranchees": 1, "ouvertes": 0}
    assert c.brain.etat()["attributes"]["contradictions"] == []
    assert c.memoire.db.execute("SELECT COUNT(*) FROM contradictions").fetchone()[0] == 1  # rien supprimé


def test_evenement_contradiction(tmp_path):
    c = faire(tmp_path)
    publies = []

    async def pub():
        publies.append(1)
    c.publier_brain = pub
    r = run(main.Core.contradiction(c, {"ids": [], "req_id": "t"}))
    assert r["ok"] and r["req_id"] == "t" and publies
