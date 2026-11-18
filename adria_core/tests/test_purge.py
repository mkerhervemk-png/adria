import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from memoire_agent import couper  # noqa: E402
from purge import a_purger, date_ligne  # noqa: E402
from store import Memoire  # noqa: E402

NOW = datetime(2026, 11, 18, 3, 30)


def ts(heures: float) -> str:
    return str(int((NOW - timedelta(hours=heures)).timestamp()))


def test_journal_48h_sans_horodatage_et_fiche():
    items = [
        {"uid": "a", "summary": "15/11 08:00 x", "description": ts(60)},
        {"uid": "b", "summary": "17/11 08:00 y", "description": ts(20)},
        {"uid": "c", "summary": "colis sans date", "description": ""},
        {"uid": "d", "summary": "17/11 colis", "description": ""},
        {"uid": "e", "summary": "16/11 z", "description": ts(50) + "|f=2|act=FAIT"},
        {"uid": "f", "summary": "vieux fini", "description": ts(90), "status": "completed"},
    ]
    assert {i["uid"] for i in a_purger("todo.adria_journal", items, NOW)} == {"a", "c", "e"}


def test_journal_plafond_200():
    items = [{"uid": str(k), "summary": "17/11 x", "description": ts(k / 10)} for k in range(210)]
    out = {i["uid"] for i in a_purger("todo.adria_journal", items, NOW)}
    assert len(out) == 10 and "209" in out and "0" not in out


def test_memoire_perso_7_jours():
    items = [{"uid": "a", "summary": "[mael|09/11] x"}, {"uid": "b", "summary": "[iwan|12/11] y"},
             {"uid": "c", "summary": "sans date"}, {"uid": "d", "summary": "[ryan|20/12] l'an passé"}]
    assert {i["uid"] for i in a_purger("todo.adria_memoire_perso", items, NOW)} == {"a", "d"}


def test_memoire_calendrier_obs_passes():
    items = [{"uid": "a", "summary": "[calendrier] RDV 17/11"},
             {"uid": "b", "summary": "[obs] 18/11 aujourd'hui"},
             {"uid": "c", "summary": "[savoir] 01/01 garde"},
             {"uid": "d", "summary": "[calendrier] 05/01 à venir"}]
    # 18/11 corrigé : une date de janvier vue en novembre = janvier prochain, pas purgée
    assert {i["uid"] for i in a_purger("todo.adria_memoire", items, NOW)} == {"a"}


def test_15_jours_et_dates():
    auj = NOW.date()
    assert date_ligne({"summary": "[mar. 17/11/2026] x"}, auj).isoformat() == "2026-11-17"
    assert date_ligne({"summary": "x", "description": "2026-10-01"}, auj).isoformat() == "2026-10-01"
    assert date_ligne({"summary": "x", "description": "a;dt=2026-10-02;b"}, auj).isoformat() == "2026-10-02"
    assert date_ligne({"summary": "20/12 x"}, auj).year == 2025
    items = [{"uid": "a", "summary": "[ven. 31/10/2026] vieux"}, {"uid": "b", "summary": "[lun. 10/11/2026] récent"},
             {"uid": "c", "summary": "rien"}]
    assert {i["uid"] for i in a_purger("todo.adria_jarvis_episodes", items, NOW)} == {"a"}
    assert a_purger("todo.adria_temoin", items, NOW) == []


def test_garde_fou_copie(tmp_path):
    m = Memoire(str(tmp_path / "t.db"))
    it = {"uid": "a", "summary": "ligne", "description": "123"}
    assert not m.a_la_copie("adria_journal", it)
    m.ajouter("adria_journal", [it])
    assert m.a_la_copie("adria_journal", it)
    assert not m.a_la_copie("adria_journal", {**it, "description": "autre"})


def test_couper_propre():
    t = "Iwan est parti. " * 20
    c = couper(t)
    assert len(c) <= 230 and c.endswith(".")
    c = couper("mot " * 100)
    assert len(c) <= 230 and c.endswith("…") and not c.endswith(" …")
    assert couper("court") == "court"
