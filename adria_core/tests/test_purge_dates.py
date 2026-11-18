import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from purge import purge_memoire  # noqa: E402


def _ids(items, maintenant):
    return [i["summary"] for i in purge_memoire(items, maintenant)]


def test_date_de_janvier_vue_en_novembre_n_est_pas_passee():
    m = datetime(2026, 11, 18, 3, 30)
    items = [{"summary": "[calendrier] 05/01 rentrée", "status": "needs_action"},
             {"summary": "[obs] 10/11 vu", "status": "needs_action"},
             {"summary": "[calendrier] 25/11 rdv", "status": "needs_action"}]
    assert _ids(items, m) == ["[obs] 10/11 vu"]


def test_decembre_vu_en_janvier_est_passe():
    m = datetime(2027, 1, 10, 3, 30)
    assert _ids([{"summary": "[obs] 20/12 noël", "status": "needs_action"}], m) == ["[obs] 20/12 noël"]


def test_stats_par_profondeur():
    from store import Memoire
    mem = Memoire(":memory:")
    mem.noter_ia("gemini", "interactif", True, 2.0, None, quand=1000)
    mem.noter_ia("gemini", "rapide", True, 14.0, None, quand=1000)
    s = mem.stats_ia_profondeur(500, maintenant=1100)
    assert s["interactif"]["gemini"]["moy_s"] == 2.0 and s["rapide"]["gemini"]["max_s"] == 14.0
