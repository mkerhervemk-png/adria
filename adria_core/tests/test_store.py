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


def test_archive_et_dates():
    from store import lire_archive
    m = Memoire(":memory:")
    items = lire_archive("[2026-10-02] [souvenir] Sortie au Val-André en famille\nligne sans format\n")
    assert len(items) == 1
    m.ajouter("archive/souvenir", items)
    r = m.chercher("tu te souviens du Val-André ?")
    assert r and r[0]["date"] == "2026-10-02" and r[0]["source"] == "archive/souvenir"


def test_doublon_liste_et_archive():
    m = Memoire(":memory:")
    m.ajouter("adria_souvenirs", [{"summary": "Sortie au Val-André en famille"}])
    m.ajouter("archive/souvenir", [{"date": "2026-10-02", "summary": "Sortie au Val-André en famille"}])
    assert len(m.chercher("Val-André", 5)) == 1


def test_historique():
    from zoneinfo import ZoneInfo
    from store import lignes_historique
    serie = [[
        {"entity_id": "person.ryan", "state": "home", "last_changed": "2026-11-10T16:30:00+00:00"},
        {"state": "home", "last_changed": "2026-11-10T16:31:00+00:00"},
        {"state": "unavailable", "last_changed": "2026-11-10T17:00:00+00:00"},
        {"state": "not_home", "last_changed": "2026-11-11T06:10:00+00:00"},
    ]]
    items = lignes_historique(serie, {"person.ryan": "Ryan"}, ZoneInfo("Europe/Paris"))
    assert [i["summary"] for i in items] == ["10/11 17:30 Ryan : à la maison", "11/11 07:10 Ryan : absent"]
    m = Memoire(":memory:")
    m.ajouter("historique", items)
    assert m.ajouter("historique", items) == 0  # ré-import sans doublon
    assert m.chercher("Ryan absent")[0]["date"] == "2026-11-11"


def test_migration_base_v010(tmp_path):
    import sqlite3
    chemin = str(tmp_path / "v010.db")
    db = sqlite3.connect(chemin)
    db.executescript("""
        CREATE TABLE lignes (id INTEGER PRIMARY KEY, source TEXT NOT NULL, uid TEXT,
            resume TEXT NOT NULL, detail TEXT, empreinte TEXT NOT NULL UNIQUE,
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
        CREATE VIRTUAL TABLE lignes_fts USING fts5(resume, detail,
            tokenize = 'unicode61 remove_diacritics 2');
    """)
    db.close()
    m = Memoire(chemin)
    m.ajouter("historique", [{"date": "2026-11-01", "summary": "Lampe salon : allumé"}])
    assert m.chercher("lampe salon")[0]["date"] == "2026-11-01"
