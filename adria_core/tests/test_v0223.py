"""v0.22.3 — réponse de secours complète, délai 35 s, contradictions sans faux positifs (20 cas réels)."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import brain as BR  # noqa: E402
import fiabilite as FI  # noqa: E402
from test_ascension import faire  # noqa: E402


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


# Les 20 « contradictions » relevées en production le 25/11 (textes tels que publiés, abrégés à 120 car.).
FAUX_POSITIFS = [
    ("[lun. 02/11/2026] Iwan sorti à 05:42, Mael et Vanessa en allers-retours scolaires. Inster débranchée (79%), Tempo bleu, ",
     "[mar. 03/11/2026] Iwan part tôt, Mael et Vanessa au collège l'après-midi, tous rentrés en soirée. Quart repos, Tempo ble"),
    ("[dim. 01/11/2026] Mael en repos, Tempo bleu, météo nuageuse. Iwan sort, Mael et Vanessa en déplacements intermittents. I",
     "[lun. 02/11/2026] Iwan sorti à 05:42, Mael et Vanessa en allers-retours scolaires. Inster débranchée (79%), Tempo bleu, "),
    ("light.salon|off|dim|weekend|14:18", "light.salon|off|dim|weekend|23:50 — 2026-11-15;src=physique_ou_voix"),
    ("light.salon|off|dim|weekend|14:18", "light.salon|off|dim|weekend|23:50"),
    ("light.salon|off|dim|weekend|14:18", "light.salon|off|dim|weekend|16:13 — 2026-11-15;src=appli"),
    ("light.salon|off|dim|weekend|16:13 — 2026-11-15;src=appli", "light.salon|off|dim|weekend|23:50 — 2026-11-15;src=physique_ou_voix"),
    ("light.salon|off|dim|weekend|16:13 — 2026-11-15;src=appli", "light.salon|off|dim|weekend|23:50"),
    ("light.salon|off|dim|weekend|14:18", "light.salon|off|dim|weekend|16:13"),
    ("light.salon|off|dim|weekend|16:13", "light.salon|off|dim|weekend|23:50 — 2026-11-15;src=physique_ou_voix"),
    ("light.salon|off|dim|weekend|16:13", "light.salon|off|dim|weekend|23:50"),
    ("16/11 06:00 ADRIA a décidé : observer seulement pour famille_mobile (le score d’importance était trop bas pour déranger)",
     "24/11 18:01 ADRIA a décidé : observer seulement pour planning_agenda (le score d’importance était trop bas pour déranger"),
    ("16/11 06:00 ADRIA a décidé : observer seulement pour famille_mobile (le score d’importance était trop bas pour déranger)",
     "19/11 18:00 ADRIA a décidé : observer seulement pour lumiere_ambiance (le score d’importance était trop bas pour dérange"),
    ("19/11 18:00 ADRIA a décidé : observer seulement pour lumiere_ambiance (le score d’importance était trop bas pour dérange",
     "24/11 18:01 ADRIA a décidé : observer seulement pour planning_agenda (le score d’importance était trop bas pour déranger"),
    ("19/11 12:00 ADRIA a décidé : observer seulement pour securite_cour (motif interne : ras_ia)",
     "23/11 06:00 ADRIA a décidé : observer seulement pour securite_absence (motif interne : deja_72h)"),
    ("19/11 12:00 ADRIA a décidé : observer seulement pour securite_cour (motif interne : ras_ia)",
     "21/11 06:00 ADRIA a décidé : observer seulement pour securite_cour (motif interne : deja_72h)"),
    ("[dim. 15/11/2026] Iwan est parti à 05h42, Ryan est sorti de 13h00 à 16h57. Mael est au repos. Tempo bleu, météo partiell",
     "[jeu. 12/11/2026] Mael en repos. Ryan au lycée puis Saint-Michel. Iwan absent dès l'aube, internet rétabli pour lui sur "),
    ("light.salon|off|dim|weekend|16:13 — 2026-11-15;src=appli", "light.salon|on|dim|weekend|23:46"),
    ("light.salon|off|dim|weekend|16:13", "light.salon|on|dim|weekend|23:46"),
    ("📷 10:11 Personne de la maison : une femme ferme le coffre de sa voiture, tenant un sac.",
     "📷 12:02 Personne de la maison ferme le coffre de sa voiture garée dans l'allée, tandis qu'une autre personne s'apprête à"),
    ("24/11 08:44 Ryan part de la maison (not_home)", "24/11 10:26 Mael part de la maison (not_home)"),
]


def test_les_20_faux_positifs_reels_ne_sont_plus_signales():
    assert len(FAUX_POSITIFS) == 20
    for a, b in FAUX_POSITIFS:
        lignes = FI.annoter([{"texte": a, "source": "adria_journal"}, {"texte": b, "source": "adria_journal"}])
        assert FI.contradictions(lignes) == [], (a, b)


def test_vraies_contradictions_toujours_signalees():
    cas = [("Mael a changé les pneus du 5008 à 45000 km", "Mael a changé les pneus du 5008 à 52000 km"),
           ("Le lave-linge du garage fonctionne bien", "Le lave-linge du garage ne fonctionne pas"),
           # même horodatage, valeurs différentes : toujours une contradiction
           ("24/11 08:44 Inster chargée à 80 % au garage", "24/11 08:44 Inster chargée à 35 % au garage")]
    for a, b in cas:
        lignes = FI.annoter([{"texte": a, "source": "adria_memoire_perso"}, {"texte": b, "source": "adria_journal"}])
        assert len(FI.contradictions(lignes)) == 1, (a, b)


def test_reponse_d_ia_jamais_comparee():
    lignes = FI.annoter([{"texte": "Mael a changé les pneus du 5008 à 45000 km", "source": "adria_memoire_perso"},
                         {"texte": "Mael a changé les pneus du 5008 à 52000 km", "source": "general"}])
    assert FI.contradictions(lignes) == []


def test_secours_reprend_les_outils(tmp_path, monkeypatch):
    """Cas réel du 25/11 19h44 : etat_core consulté, puis délai dépassé -> plus jamais « je ne sais pas »."""
    c = faire(tmp_path, [{"action": "outil", "outil": "etat_core", "args": {}}, None])
    r = run(c.brain.repondre({"question": "Quelle version d'ADRIA Core tourne ?", "qui": "Mael"}))
    assert r["texte"].startswith("Voici ce que j'ai vérifié : ADRIA Core 0.22.0")
    assert "0 erreur(s) ouverte(s)" in r["texte"] and r["statut"] == "sait"
    assert [p["source"] for p in r["preuves"]] == ["etat_core"]


def test_secours_outils_puis_memoire(tmp_path):
    c = faire(tmp_path, [{"action": "outil", "outil": "etat_entite", "args": {"entite": "light.salon"}}, None])
    r = run(c.brain.repondre({"question": "pneus 5008", "qui": "Mael"}))  # mémoire : pneus (fiable)
    assert r["texte"].startswith("Voici ce que j'ai vérifié : x : on") and "En mémoire :" in r["texte"]
    assert r["preuves"][0]["source"] == "etat_entite"


def test_secours_memoire_seule_inchange(tmp_path):
    c = faire(tmp_path, [None])
    r = run(c.brain.repondre({"question": "gâteau chocolat Iwan", "qui": "Mael"}))
    assert r["texte"].startswith("Voici ce que j'ai trouvé")


def test_resume_outil_sans_brut_ni_json_brut():
    t = BR.resume_outil({"outil": "inconnu", "texte": '{"a": [1, "b"]}'})
    assert "{" not in t and '"' not in t
    assert BR.resume_outil({"outil": "agenda", "brut": ["2026-11-26 09:00 Patin"], "texte": ""}) == \
        "agenda : 2026-11-26 09:00 Patin"


def test_brut_jamais_envoye_au_modele(tmp_path):
    c = faire(tmp_path, [{"action": "outil", "outil": "etat_core", "args": {}},
                         {"action": "repondre", "type": "maison", "reponse": "0.22.0", "preuves": ["O1"]}])
    run(c.brain.repondre({"question": "version", "qui": "Mael"}))
    assert "brut" not in c.routeur.prompts[1]
    assert json.dumps(c.brain.dernier)  # suivi sérialisable, sans données brutes


def test_delais():
    assert BR.DELAI_TEXTE == 35.0 and BR.DELAI_VOIX == 15.0
