import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import maintenance as MA  # noqa: E402
from store import Memoire  # noqa: E402


def e(eid, state, **attrs):
    return {"entity_id": eid, "state": state, "attributes": attrs}


SAIN = [
    e("automation.a", "on", friendly_name="A"),
    e("sensor.x", "unavailable"),
    e("binary_sensor.frigate_sante_camera_salon_probleme", "off"),
    e("sensor.garage_detecteur_batterie", "70", device_class="battery"),
    e("sensor.telephone_de_iwan_battery_level", "5", device_class="battery"),
    e("update.vlc_mise_a_jour", "off"),
    e("sensor.192_168_1_1_adria_duree_coupure_reseau_pc_ha_24h", "0.19"),
    e("input_text.adria_telephones_injoignables", ""),
    e("sensor.ia_health_score", "100"),
]


def test_maison_saine():
    points, indispo = MA.diagnostiquer(SAIN, {}, [1, 1])
    assert points == [] and indispo == 1
    assert MA.niveau_global(points) == "ok"


def test_telephone_exclu_des_piles():
    points, _ = MA.diagnostiquer(SAIN)
    assert not any(p["cle"] == "piles" for p in points)


def test_problemes_detectes():
    etats = SAIN + [
        e("automation.b", "unavailable", friendly_name="B cassée"),
        e("binary_sensor.frigate_sante_cameras_de_la_cour_probleme", "on", friendly_name="Cour Problème"),
        e("sensor.sirene_toit_batterie", "15", device_class="battery", friendly_name="Sirène toit"),
        e("update.frigate_mise_a_jour", "on", friendly_name="Frigate Mise à jour"),
    ]
    etats[6] = e("sensor.192_168_1_1_adria_duree_coupure_reseau_pc_ha_24h", "1.0")
    etats[7] = e("input_text.adria_telephones_injoignables", "Vanessa@1911,Iwan@1911")
    etats[8] = e("sensor.ia_health_score", "50")
    core = {"derniere_purge": {"erreurs": ["x"]}, "derniere_consolidation": {"ok": False, "raison": "IA sans réponse"},
            "stats_ia": {"gemini": {"appels": 10, "ok": 5}}}
    points, _ = MA.diagnostiquer(etats, core)
    cles = {p["cle"] for p in points}
    assert cles == {"automatisations_ko", "cameras", "piles", "mises_a_jour", "reseau", "telephones",
                    "sante_ia", "core_purge", "core_consolidation", "core_ia"}
    assert MA.niveau_global(points) == "probleme"
    txt = {p["cle"]: p["texte"] for p in points}
    assert "B cassée" in txt["automatisations_ko"] and "Sirène toit 15 %" in txt["piles"]
    assert "Vanessa, Iwan" in txt["telephones"] or "Iwan, Vanessa" in txt["telephones"]
    assert "60 min" in txt["reseau"]


def test_indisponibles_seulement_si_hausse_nette():
    etats = [e(f"sensor.u{i}", "unavailable") for i in range(120)]
    pts, n = MA.diagnostiquer(etats, {}, [100, 101, 99])
    assert n == 120 and any(p["cle"] == "indisponibles" for p in pts)
    pts, _ = MA.diagnostiquer(etats, {}, [110])
    assert not pts
    pts, _ = MA.diagnostiquer(etats, {}, [])  # pas encore de référence
    assert not pts


def test_comparer_et_message():
    pts = [{"cle": "piles", "niveau": "surveiller", "texte": "pile faible"},
           {"cle": "cameras", "niveau": "probleme", "texte": "caméra"}]
    nouveaux, regles = MA.comparer(pts, ["piles", "reseau"])
    assert [p["cle"] for p in nouveaux] == ["cameras"] and regles == ["reseau"]
    msg = MA.message(nouveaux, regles)
    assert msg.startswith("🔴 caméra") and "✅ réglé : réseau" in msg


class FauxCore:
    def __init__(self, etats, interrupteur="on"):
        self.memoire = Memoire(":memory:")
        self.etats = etats
        self.interrupteur = interrupteur
        self.envois = []
        self.derniere_purge = {"erreurs": []}
        self.consolidation = type("C", (), {"resume": lambda s: {"ok": True}})()

    async def appel(self, methode, chemin, donnees=None, delai=60):
        if chemin == "/states" and methode == "GET":
            return self.etats
        self.envois.append((chemin, donnees))
        return {}

    async def etat(self, eid):
        return self.interrupteur


def notifs(c):
    return [d for ch, d in c.envois if ch.startswith("/services/")]


def test_agent_notifie_seulement_le_nouveau():
    etats = SAIN + [e("update.frigate_mise_a_jour", "on", friendly_name="Frigate")]
    c = FauxCore(etats)
    a = MA.AgentMaintenance(c, "notify.mobile_app_x")
    r = asyncio.run(a.diagnostic())
    assert r["niveau"] == "surveiller" and r["notifie"] and len(notifs(c)) == 1
    r = asyncio.run(a.diagnostic())  # le lendemain, rien de neuf -> pas de notification
    assert not r["notifie"] and len(notifs(c)) == 1
    c.etats = SAIN  # réglé -> une notification « réglé »
    r = asyncio.run(a.diagnostic())
    assert r["notifie"] and "réglé" in notifs(c)[-1]["message"] and r["niveau"] == "ok"


def test_essai_et_interrupteur_off():
    etats = SAIN + [e("update.frigate_mise_a_jour", "on")]
    c = FauxCore(etats, interrupteur="off")
    a = MA.AgentMaintenance(c, "notify.mobile_app_x")
    asyncio.run(a.diagnostic({"essai": "true"}))
    assert c.memoire.meta("maintenance_cles") is None and not notifs(c)
    r = asyncio.run(a.diagnostic())
    assert not r["notifie"] and not notifs(c)
    assert any(ch == "/states/sensor.adria_core_maintenance" for ch, _ in c.envois)
