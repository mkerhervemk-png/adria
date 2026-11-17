import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from periode import periode  # noqa: E402
from store import Memoire  # noqa: E402

AUJ = date(2026, 11, 17)  # un mardi


def p(q):
    r = periode(q, AUJ)
    return (r[0].isoformat(), r[1].isoformat()) if r else None


def test_periodes():
    assert p("Raconte-moi octobre") == ("2026-10-01", "2026-10-31")
    assert p("en décembre ?") == ("2025-12-01", "2025-12-31")
    assert p("en novembre") == ("2026-11-01", "2026-11-17")
    assert p("la semaine dernière") == ("2026-11-09", "2026-11-15")
    assert p("cette semaine") == ("2026-11-16", "2026-11-17")
    assert p("le week-end dernier") == ("2026-11-14", "2026-11-15")
    assert p("le mois dernier") == ("2026-10-01", "2026-10-31")
    assert p("hier soir") == ("2026-11-16", "2026-11-16")
    assert p("avant-hier") == ("2026-11-15", "2026-11-15")
    assert p("mardi dernier") == ("2026-11-10", "2026-11-10")
    assert p("lundi dernier") == ("2026-11-16", "2026-11-16")
    assert p("le 24/10") == ("2026-10-24", "2026-10-24")
    assert p("le 24 octobre") == ("2026-10-24", "2026-10-24")
    assert p("le 20/12") == ("2025-12-20", "2025-12-20")
    assert p("qu'avait-on décidé pour le préchauffage ?") is None


def base():
    m = Memoire(":memory:")
    m.ajouter("archive/observation", [{"date": "2026-10-24", "summary": f"obs {i}"} for i in range(30)])
    m.ajouter("archive/episode", [
        {"date": "2026-10-24", "summary": "[sam. 24/10] Ryan rentré en matinée, Inster branchée à 21h22"},
        {"date": "2026-10-25", "summary": "[dim. 25/10] Journée calme, Tempo bleu"},
        {"date": "2026-11-02", "summary": "[lun. 02/11] Reprise de l'école"},
    ])
    m.ajouter("archive/souvenir", [{"date": "2026-10-26", "summary": "Semaine du 19/10 : vacances, sortie au Val-André"}])
    m.ajouter("archive/memoire_perso", [{"date": "2026-10-24", "summary": "[iwan|24/10] badminton"}])
    return m


def test_raconter_un_mois_priorite_et_ordre():
    r = base().chercher("Raconte-moi octobre", 4, auj=AUJ)
    assert [x["source"] for x in r].count("archive/observation") == 0  # résumés d'abord
    assert [x["date"] for x in r] == sorted(x["date"] for x in r)
    assert all(x["date"].startswith("2026-10") for x in r)


def test_periode_et_sujet():
    r = base().chercher("en octobre, quand l'Inster a été branchée ?", 3, auj=AUJ)
    assert r and "Inster" in r[0]["texte"]


def test_periode_exclusion():
    r = base().chercher("le 24/10", 10, ["archive/memoire_perso"], auj=AUJ)
    assert all(x["source"] != "archive/memoire_perso" for x in r)


def test_sans_periode_inchange():
    assert base().chercher("Val-André", 3, auj=AUJ)[0]["source"] == "archive/souvenir"
