import asyncio
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import formulaires as F  # noqa: E402

PDF = os.path.join(os.path.dirname(__file__), "fixtures", "questionnaire_irm.pdf")
L, H = 595.32, 841.92


def ligne(y0, y1):
    return {"libelle": f"q{y0}", "type": "oui_non", "cle": "autre", "question": f"Question {y0} ?",
            "zone_oui": [round(y0 / H * 1000), 795, round(y1 / H * 1000), 865],
            "zone_non": [round(y0 / H * 1000), 867, round(y1 / H * 1000), 937]}


GEMINI = {"titre": "Questionnaire IRM", "champs": [
    *[ligne(a, b) for a, b in [(164, 190), (190, 216), (216, 242), (242, 293), (293, 313), (313, 339), (339, 365)]],
    {"libelle": "Taille", "type": "texte", "cle": "autre", "question": "Ta taille ?", "zone": [490, 800, 515, 940]},
    {"libelle": "Poids", "type": "texte", "cle": "autre", "question": "Ton poids ?", "zone": [536, 800, 560, 940]},
    {"libelle": "Nom et prénom", "type": "texte", "cle": "nom_prenom", "question": "Nom ?", "zone": [775, 360, 795, 600]},
    {"libelle": "Fait le", "type": "texte", "cle": "date_jour", "question": "Date ?", "zone": [793, 115, 810, 230]},
    {"libelle": "Signature", "type": "signature", "zone": [815, 50, 860, 300]},
]}


def mots():
    xml = subprocess.run(["pdftotext", "-bbox-layout", PDF, "-"], capture_output=True, text=True).stdout
    return F.mots_pdf(xml)


def test_lire_json():
    assert F.lire_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert F.lire_json("bla {\"b\": [1]} fin") == {"b": [1]}
    assert F.lire_json("rien") == {}


def test_vers_points():
    assert F.vers_points([0, 0, 1000, 500], 600, 800) == (0, 0, 300, 800)
    assert F.vers_points([10, 900, 5, 950], 600, 800) is None
    assert F.vers_points(None, 600, 800) is None


def test_valeur_profil():
    p = {"nom": "Kerherve", "prenom": "Mael", "ville": "Saint-Brieuc"}
    from datetime import date
    assert F.valeur_profil("nom_prenom", p, date(2026, 11, 18)) == "KERHERVE Mael"
    assert F.valeur_profil("date_jour", p, date(2026, 11, 18)) == "18/11/2026"
    assert F.valeur_profil("lieu_signature", p, date(2026, 11, 18)) == "Saint-Brieuc"
    assert F.valeur_profil("telephone", p, date(2026, 11, 18)) is None
    assert F.valeur_profil("autre", p, date(2026, 11, 18)) is None


def test_placement_irm():
    champs = F.normaliser(GEMINI["champs"], 1, mots(), L, H)
    assert len(champs) == 11  # signature ignorée
    q1 = champs[0]
    assert abs(q1["non"][0] - 533) < 5 and abs(q1["oui"][0] - 488) < 5  # centrés sur en-têtes OUI/NON
    nom = next(c for c in champs if c["cle"] == "nom_prenom")
    assert nom["masque"] and abs(nom["x"] - 216) < 3  # écrit sur les pointillés
    taille = next(c for c in champs if c["libelle"] == "Taille")
    assert taille["x"] > 474 and abs(taille["base"] - 427.5) < 3  # après « Taille : », même ligne


def test_traiter_bout_en_bout(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "DOSSIER", str(tmp_path))
    monkeypatch.setattr(F, "ATTENTE_REPONSE", 2)
    (tmp_path / "entree").mkdir()
    src = tmp_path / "entree" / "CONSENTEMENT PATIENT.pdf"
    src.write_bytes(open(PDF, "rb").read())

    class FauxHA:
        def __init__(self):
            self.notifs = []

        async def ai_task_vision(self, prompt, tache, medias):
            assert medias[0]["media_content_id"].endswith("/p-1.png")
            return json.dumps(GEMINI)

        async def appel(self, m, chemin, d=None, delai=60):
            self.notifs.append(d)
            acts = (d.get("data") or {}).get("actions") or []
            if acts:  # Mael répond depuis le téléphone
                a = acts[0]["action"]
                asyncio.get_running_loop().call_later(0.01, agent.recevoir_action,
                    {"action": "REPLY", "reply_text": "1,87 m" if "taille" in d["message"].lower() else "83 kg"}
                    if a == "REPLY" else {"action": a.rsplit("_", 1)[0] + "_TOUTNON"})
            return {}

    ha = FauxHA()
    agent = F.AgentFormulaires(ha, {"nom": "Kerherve", "prenom": "Mael"})
    res = asyncio.run(agent.traiter(str(src)))
    assert res["ok"], res
    assert res["ecrits"] == 11 and res["vides"] == 0 and res["auto"] == 2
    sortie = tmp_path / res["sortie"]
    texte = subprocess.run(["pdftotext", str(sortie), "-"], capture_output=True, text=True).stdout
    assert "KERHERVE Mael" in texte and "1,87 m" in texte and "83 kg" in texte
    assert (tmp_path / "entree" / "traites").exists() and not src.exists()
    assert sortie.with_suffix(".png").exists()
    # aucune réponse ne reste dans l'agent
    assert agent.attentes == {} and agent.en_cours is None
    assert "83 kg" not in json.dumps(res)


def test_echec_range_sans_reboucler(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "DOSSIER", str(tmp_path))
    (tmp_path / "entree").mkdir()
    src = tmp_path / "entree" / "x.pdf"
    src.write_bytes(open(PDF, "rb").read())

    class FauxHA:
        async def ai_task_vision(self, *a):
            return "pas de json"

        async def appel(self, *a, **k):
            return {}

    res = asyncio.run(F.AgentFormulaires(FauxHA()).traiter(str(src)))
    assert not res["ok"] and not src.exists() and (tmp_path / "entree" / "echecs").exists()
