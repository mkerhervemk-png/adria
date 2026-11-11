import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from store import Memoire, mots_cles  # noqa: E402


def memoire():
    m = Memoire(":memory:")
    m.ajouter("adria_journal", [
        {"uid": "1", "summary": "14/09 Mael a validé la charge Inster à 90 % les jours bleus"},
        {"uid": "2", "summary": "15/09 Poubelle jaune sortie par Ryan"},
        {"uid": "3", "summary": "16/09 Décision : préchauffage Inster si température sous 12 °C"},
    ], datetime(2026, 9, 16))
    m.ajouter("adria_memoire_perso", [
        {"uid": "4", "summary": "[iwan|16/09] badminton samedi"},
    ], datetime(2026, 9, 16))
    return m


def test_mots_cles_sans_accents_ni_mots_vides():
    assert mots_cles("Qu'est-ce qu'on avait décidé pour le préchauffage ?") == ["decid", "prech"]


def test_recherche_trouve_la_decision():
    r = memoire().chercher("qu'avait-on décidé pour le préchauffage de l'Inster ?", 3)
    assert r and "préchauffage" in r[0]["texte"]


def test_rien_n_est_efface_et_pas_de_doublon():
    m = memoire()
    nouvelles = m.ajouter("adria_journal", [
        {"uid": "1", "summary": "14/09 Mael a validé la charge Inster à 90 % les jours bleus"},
    ], datetime(2026, 11, 11))
    assert nouvelles == 0
    assert m.stats()["lignes"] == 4


def test_exclure_une_source():
    m = memoire()
    assert m.chercher("badminton", 5)
    assert not m.chercher("badminton", 5, ["adria_memoire_perso"])


def test_question_vide():
    assert memoire().chercher("le la les", 5) == []
