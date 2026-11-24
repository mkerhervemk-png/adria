import asyncio
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from monde import anticiper, evenements, vue  # noqa: E402
from notifs import AgentNotifs, analyser, partie_bilan  # noqa: E402

TZ = ZoneInfo("Europe/Paris")
M = datetime(2026, 11, 24, 21, 0, tzinfo=TZ)


def E(i, s, **a):
    return {"entity_id": i, "state": s, "attributes": a}


EVS = evenements({"service_response": {
    "calendar.ryan": {"events": [{"start": "2026-11-25T09:00:00+01:00", "end": "2026-11-25T11:30:00+01:00",
                                  "summary": "Stage - matin", "location": "DOMITYS, Saint-Brieuc"}]},
    "calendar.aelia": {"events": [{"start": "2026-11-25T10:00:00+01:00", "summary": "Psychomotricienne"},
                                  {"start": "2026-11-25", "summary": "journée"}]},
    "calendar.mael": {"events": [{"start": "2026-11-25T06:00:00+01:00", "summary": "Matin"}]}}})


def test_vue():
    etats = [E("sensor.adria_ou_ryan", "maison|0.8|x"), E("input_boolean.ryan_en_stage", "on"),
             E("sensor.inster_ev_battery_level", "61"), E("input_select.quart_mael", "repos")]
    v = vue(etats, EVS, M)
    assert v["gens"]["ryan"]["prochain"].endswith("Stage - matin")
    assert "Ryan en stage" in v["texte"] and "Inster 61%" in v["texte"]
    assert "journée" not in str(EVS.get("aelia"))  # les événements « journée entière » sont écartés


def test_anticipations():
    etats = [E("sensor.inster_ev_battery_level", "22"), E("binary_sensor.inster_ev_battery_plug", "off")]
    pluie = [{"datetime": "2026-11-25T09:00:00+01:00", "precipitation": 1.2}]
    anniv = [{"start": "2026-11-28", "summary": "Mamie - Anniversaire"}]
    a = {x["cle"].split("_")[0]: x["texte"] for x in anticiper(etats, EVS, anniv, pluie, M)}
    assert "brancher" in a["inster"] and "Pluie prévue vers 09h00" in a["pluie"]
    assert "Psychomotricienne" in a["adresse"] and "dans 4 jours" in a["anniv"]
    assert "Matin" not in str(a)  # un quart n'est pas un trajet à anticiper


def test_pas_d_anticipation_inutile():
    etats = [E("sensor.inster_ev_battery_level", "80")]
    assert anticiper(etats, {}, [], [], M) == []


def test_notifs_analyse_et_bilan():
    t = lambda h, m: datetime(2026, 11, 24, h, m, tzinfo=TZ).isoformat()  # noqa: E731
    notes = [{"t": t(8, 0), "titre": "Caméra cour"}, {"t": t(8, 10), "titre": "Caméra cour"},
             {"t": t(23, 0), "titre": "Réseau"}, {"t": t(12, 0), "titre": "🚨 Intrusion"},
             {"t": t(14, 0), "titre": "Devoirs"}, {"t": t(15, 0), "titre": "Colis"}]
    s = analyser(notes, M)
    assert s["aujourdhui"] == 6 and s["doublons_7j"] == 1 and s["heures_calmes_aujourdhui"] == 1
    assert s["au_dela_du_budget_aujourdhui"] == 2  # 5 non urgentes - budget 3
    many = [{"t": (M - timedelta(hours=i)).isoformat(), "titre": "Caméra cour"} for i in range(8)]
    assert "Proposition : regrouper « Caméra cour »" in partie_bilan(analyser(many, M))


class _Mem:
    def __init__(self):
        self.m = {}

    def meta(self, k, v=None):
        if v is None:
            return self.m.get(k)
        self.m[k] = v


class _Core:
    def __init__(self, inter="on"):
        self.memoire, self.inter, self.pub = _Mem(), inter, []

    async def etat(self, e):
        return self.inter

    async def publier_capteur(self, e, d):
        self.pub.append((e, d))


def test_recevoir_filtre():
    c = _Core()
    n = AgentNotifs(c)
    asyncio.run(n.recevoir({"domain": "notify", "service": "mobile_app_telephone_mael_sm_s921b",
                            "service_data": {"title": "Test", "data": {"tag": "x"}}}))
    asyncio.run(n.recevoir({"domain": "notify", "service": "alexa_media", "service_data": {"title": "Echo"}}))
    asyncio.run(n.recevoir({"domain": "light", "service": "turn_on"}))
    assert len(n._notes()) == 1 and c.pub[-1][1]["state"] == 1
    c2 = _Core("off")
    n2 = AgentNotifs(c2)
    asyncio.run(n2.recevoir({"domain": "notify", "service": "mobile_app_telephone_mael_x", "service_data": {}}))
    assert n2._notes() == []
