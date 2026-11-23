import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from analyse import AgentAnalyse, detecter  # noqa: E402
from veille import AgentVeille, resumer  # noqa: E402


def E(i, s, **a):
    return {"entity_id": i, "state": s, "attributes": a}


BASE = [E("person.mael_kerherve", "not_home"), E("person.vanessa", "not_home"),
        E("light.salon", "on", friendly_name="Salon"), E("light.piece_de_vie", "on", entity_id=["light.salon"]),
        E("light.h618a", "on"), E("sun.sun", "above_horizon"), E("light.abris_voiture", "on", friendly_name="Abri"),
        E("light.chambre_aelia_guirlande_aelia_prise_1", "on"), E("sensor.sm_t720_battery_level", "9")]


def test_detecter_tout():
    a = {x["cle"]: x["texte"] for x in detecter(BASE, datetime(2026, 11, 24, 12, 0))}
    assert set(a) == {"lumieres_maison_vide", "exterieur_jour", "guirlande_aelia", "tablette"}
    assert "Salon" in a["lumieres_maison_vide"] and "h618a" not in a["lumieres_maison_vide"]
    assert "piece_de_vie" not in a["lumieres_maison_vide"]  # groupe ignoré


def test_detecter_rien_quand_quelqu_un_est_la_le_soir():
    etats = [E("person.mael_kerherve", "home"), E("light.salon", "on"), E("sun.sun", "below_horizon"),
             E("light.chambre_aelia_guirlande_aelia_prise_1", "on"), E("sensor.sm_t720_battery_level", "80")]
    assert detecter(etats, datetime(2026, 11, 24, 20, 0)) == []


class _Mem:
    def __init__(self):
        self.m, self.lignes = {}, []

    def meta(self, k, v=None):
        if v is None:
            return self.m.get(k)
        self.m[k] = v

    def ajouter(self, source, items, maintenant=None):
        self.lignes += [(source, i) for i in items]
        return len(items)


class _Core:
    def __init__(self, etats, inter="on"):
        self.etats, self.inter, self.memoire, self.appels = etats, inter, _Mem(), []

    async def etat(self, e):
        return self.inter

    async def appel(self, m, chemin, data=None):
        if chemin == "/states":
            return self.etats
        self.appels.append((chemin, data))
        return {}


def test_analyse_une_seule_notif_par_jour():
    c = _Core([E("person.mael_kerherve", "not_home"), E("light.salon", "on")])
    ag = AgentAnalyse(c, "notify.mobile_app_x")
    r1 = asyncio.run(ag.analyser())
    r2 = asyncio.run(ag.analyser())
    notifs = [x for x in c.appels if x[0].startswith("/services/notify")]
    assert r1["notifiees"] == ["lumieres_maison_vide"] and r2["notifiees"] == [] and len(notifs) == 1


def test_analyse_off_et_essai_sans_notif():
    c = _Core([E("person.mael_kerherve", "not_home"), E("light.salon", "on")], inter="off")
    asyncio.run(AgentAnalyse(c, "notify.x").analyser())
    c2 = _Core([E("person.mael_kerherve", "not_home"), E("light.salon", "on")])
    asyncio.run(AgentAnalyse(c2, "notify.x").analyser({"essai": "true"}))
    assert not [x for x in c.appels + c2.appels if x[0].startswith("/services/")]


def test_veille_resume_et_memoire():
    etats = [E("input_select.tempo_couleur_du_jour", "rouge"),
             E("sensor.meteo_france_alert_for_department_22_22_weather_alert", "Jaune", **{"Vent violent": "Jaune", "Pluie": "Vert"}),
             E("todo.colis", "2"), E("binary_sensor.workday_sensor", "off"),
             E("sensor.tarif_edf_tempo_6kva_tarif_tempo_couleur_demain", "rouge")]
    r = resumer(etats)
    assert r["points"][0].startswith("Tempo rouge") and "Vent violent" in r["points"][1]
    assert "2 colis" in r["texte"] and "demain" not in r["texte"].lower()
    c = _Core(etats)
    asyncio.run(AgentVeille(c).veiller())
    assert c.memoire.lignes and c.memoire.lignes[0][0] == "veille"
    assert not [x for x in c.appels if x[0].startswith("/services/")]  # jamais de notification


def test_veille_rien():
    assert resumer([])["texte"] == "rien de particulier"
