import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from famille import AgentFamille, journee  # noqa: E402


def E(i, s, **a):
    return {"entity_id": i, "state": s, "attributes": a}


MARDI = datetime(2026, 11, 24, 7, 0)
EDT_IWAN = E("sensor.ed_iwan_kerherve_emploi_du_temps_aujourd_hui", "2", **{
    "Journée début": "08:10", "Journée fin": "15:20", "Emploi du temps": [
        {"start_time": "08:10", "lesson": "TECHNOLOGIE", "is_annule": False, "is_modifie": False},
        {"start_time": "14:25", "lesson": "FRANCAIS", "is_annule": True, "is_modifie": False}]})


def test_journee_normale():
    r = journee([EDT_IWAN, E("input_text.edt_iwan_fin_cours_aujourd_hui", "12:05"),
                 E("calendar.vacances_scolaires_zone_b", "off")], MARDI)
    assert r["ecole"] and "Iwan : 08:10 → 12:05 · 1 cours" in r["texte"]
    assert "Francais ❌ annulé" in r["texte"] and "Ryan : emploi du temps illisible." in r["texte"]


def test_vacances_weekend_malade():
    assert not journee([E("calendar.vacances_scolaires_zone_b", "on")], MARDI)["ecole"]
    assert not journee([], datetime(2026, 11, 28, 7, 0))["ecole"]
    r = journee([EDT_IWAN, E("input_boolean.iwan_malade", "on")], MARDI)
    assert "Iwan : malade" in r["texte"]


def test_tous_annules():
    s = E("sensor.ed_ryan_kerherve_emploi_du_temps_aujourd_hui", "1", **{"Emploi du temps": [
        {"start_time": "08:00", "lesson": "MATHS", "is_annule": True}]})
    assert "Ryan : tous les cours sont annulés." in journee([s], MARDI)["texte"]


class _Mem:
    def __init__(self):
        self.l = []

    def ajouter(self, source, items, m=None):
        self.l += items


class _Core:
    def __init__(self, etats, inter="on"):
        self.etats, self.inter, self.memoire, self.appels = etats, inter, _Mem(), []

    async def etat(self, e):
        return self.inter

    async def appel(self, m, chemin, data=None):
        if chemin == "/states":
            return self.etats
        self.appels.append((chemin, data))


def test_matin_une_notif_et_memoire(monkeypatch):
    import famille as F

    class _D(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 24, 7, 0, tzinfo=tz)
    monkeypatch.setattr(F, "datetime", _D)
    c = _Core([EDT_IWAN])
    r = asyncio.run(AgentFamille(c, "notify.mobile_app_x").matin())
    assert r["notifie"] and len([a for a in c.appels if a[0].startswith("/services/notify")]) == 1
    assert c.memoire.l and c.memoire.l[0]["uid"] == "ecole-2026-11-24"
    c2 = _Core([EDT_IWAN], inter="off")
    asyncio.run(AgentFamille(c2, "notify.x").matin())
    assert not [a for a in c2.appels if a[0].startswith("/services/")]


def test_creneau_sans_matiere():
    s = E("sensor.ed_ryan_kerherve_emploi_du_temps_aujourd_hui", "1", **{"Journée début": "08:00", "Emploi du temps": [
        {"start_time": "08:00", "end_time": "17:15", "lesson": " ", "is_annule": False, "is_modifie": False}]})
    assert "08:00 créneau sans matière (jusqu'à 17:15)" in journee([s], MARDI)["texte"]
