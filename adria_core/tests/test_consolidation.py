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


# --- v0.18.0 : consolidation réelle -----------------------------------------
import asyncio  # noqa: E402

from consolidation import AgentConsolidation  # noqa: E402


class _Mem:
    def __init__(self):
        self.copies, self.m = set(), {}

    def meta(self, k, v=None):
        if v is None:
            return self.m.get(k)
        self.m[k] = v

    def ajouter(self, source, items, maintenant=None):
        for i in items:
            self.copies.add((source, i.get("summary"), i.get("description")))
        return len(items)

    def a_la_copie(self, source, i):
        return (source, i.get("summary"), i.get("description")) in self.copies


class _Routeur:
    def __init__(self, texte):
        self.texte = texte

    async def demander(self, *a):
        return {"ok": True, "data": self.texte, "moteur": "gemini"}


class _Core:
    def __init__(self, reel, items, texte, termines=()):
        self.reel, self.items, self.termines = reel, items, list(termines)
        self.memoire, self.routeur, self.appels = _Mem(), _Routeur(texte), []

    async def etat(self, e):
        if e == "input_boolean.adria_core_consolidation":
            return "on"
        if e == "input_boolean.adria_core_consolidation_reelle":
            return "on" if self.reel else "off"
        return ""

    async def appel(self, methode, chemin, data=None):
        if "get_items" in chemin:
            lst = self.termines if data.get("status") == "completed" else self.items
            return {"service_response": {"todo.adria_memoire": {"items": lst}}}
        if "last_changed" in chemin or chemin.startswith("/states/"):
            return {"last_changed": "2026-11-18T01:00:00+01:00"}
        self.appels.append((chemin, data))
        return {}


ITEMS = [{"uid": f"u{k}", "summary": f"[habitude] fait {k}", "description": "imp=3;dt=2026-11-01;n=2"}
         for k in range(6)]
TEXTE = "\n".join(f"3|habitude|2|2026-11-01|fait fusionne numero {k}" for k in range(6))


def test_essai_n_ecrit_rien():
    c = _Core(False, ITEMS, TEXTE)
    r = asyncio.run(AgentConsolidation(c).consolider())
    assert r["essai"] and r["ok"] and c.appels == []


def test_reel_copie_puis_ecrit():
    vieux = {"uid": "t1", "summary": "[pb] ancien", "description": "src=consolide"}
    inconnu = {"uid": "t2", "summary": "[pb] jamais copie", "description": "x"}
    c = _Core(True, ITEMS, TEXTE, termines=[vieux, inconnu])
    c.memoire.ajouter("adria_memoire", [vieux])
    r = asyncio.run(AgentConsolidation(c).consolider())
    assert not r["essai"] and r["ok"]
    assert all(c.memoire.a_la_copie("adria_memoire", i) for i in ITEMS)  # copie avant écriture
    retires = [d["item"] for p, d in c.appels if p.endswith("remove_item")]
    assert retires == [["t1"]]  # la ligne sans copie reste
    assert sum(1 for p, d in c.appels if p.endswith("update_item") and d.get("status") == "completed") == 6
    ajouts = [d for p, d in c.appels if p.endswith("add_item")]
    assert len(ajouts) == 6 and "src=consolide" in ajouts[0]["description"]
    assert r["ecrit"]["ajoutes"] == 6 and r["ecrit"]["termines"] == 6


def test_reel_garde_fou_n_ecrit_pas():
    c = _Core(True, ITEMS, "3|habitude|1|2026-11-01|un seul fait reste")
    r = asyncio.run(AgentConsolidation(c).consolider())
    assert not r["ok"] and r["garde_fou"]
    assert not [1 for p, d in c.appels if p.endswith("add_item")]


def test_reel_interdits_n_ecrit_pas():
    t = TEXTE + "\n4|preference|1|2026-11-01|Mael aime la charge 21h30 le soir"
    c = _Core(True, ITEMS, t)
    r = asyncio.run(AgentConsolidation(c).consolider())
    assert not r["ok"] and "interdits" in r["raison"]
    assert not [1 for p, d in c.appels if p.endswith("add_item")]
