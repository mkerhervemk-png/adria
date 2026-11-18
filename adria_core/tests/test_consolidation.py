import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from consolidation import (appliquer_tri, construire_prompt, garde_fou, interdits_recrees,  # noqa: E402
                           lire, rien_de_nouveau, trier)

M = datetime(2026, 11, 18, 2, 15)


def test_tri_identique_au_script_ha():
    items = [
        {"uid": "r", "summary": "[res] ok lampe", "description": "dt=2026-10-01"},
        {"uid": "r2", "summary": "[res] ok lampe", "description": "dt=2026-11-10"},
        {"uid": "o1", "summary": "[obs] vu une fois", "description": "dt=2026-11-01;n=1"},
        {"uid": "o2", "summary": "[obs] vu 3 fois", "description": "dt=2026-10-10;n=3"},
        {"uid": "o3", "summary": "[obs] vu 3 fois récent", "description": "dt=2026-11-10;n=3"},
        {"uid": "p", "summary": "[obs] TV salon", "description": "imp=2;dt=2026-11-10;n=6"},
        {"uid": "p2", "summary": "[obs] trop récent", "description": "imp=2;dt=2026-11-17;n=6"},
    ]
    t = trier(items, M)
    assert t["res_vieux"] == ["r"]
    assert t["obs_vieilles"] == ["o1", "o2"]
    assert [p["uid"] for p in t["promouvoir"]] == ["p"]
    assert t["promouvoir"][0]["s"] == "[REGLE] TV salon" and "imp=5" in t["promouvoir"][0]["d"]
    reste = appliquer_tri(items, t)
    assert {i["uid"] for i in reste} == {"r2", "o3", "p", "p2"}


def test_lecture_et_garde_fou():
    txt = "voici\n4|preference|3|2026-10-01|Mael aime le salon a 20 degres\n3|habitude|Aelia patin le samedi\nbof"
    l = lire(txt, M)
    assert len(l) == 2 and l[0]["n"] == 3 and l[1]["dt"] == "2026-11-18"
    assert garde_fou(l, 4)  # moins de 5 faits
    assert not garde_fou(l * 5, 18) and garde_fou(l * 5, 30)


def test_rien_de_nouveau_et_prompt():
    items = [{"summary": "[profil] Mael", "description": "src=consolide"}]
    assert rien_de_nouveau(items, 0) and not rien_de_nouveau(items, 1)
    assert not rien_de_nouveau(items + [{"summary": "x", "description": ""}], 0)
    p = construire_prompt(items, {"VIE": "abc", "TYPE_DE_JOUR": "S" * 100}, M)
    assert "INTERDITS" in p and "VIE: abc" in p and "MEMOIRE ACTUELLE (1 faits)" in p and "S" * 61 not in p


def test_interdits():
    l = [{"fait": "Nuit Douce activee chaque soir"}, {"fait": "Mael aime l'ademe"}, {"fait": "priorisation via ADE"}]
    assert interdits_recrees(l) == ["Nuit Douce activee chaque soir", "priorisation via ADE"]
