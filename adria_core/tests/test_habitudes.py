import asyncio
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import habitudes as H  # noqa: E402
from store import Memoire, lignes_historique  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

AUJ = date(2026, 12, 20)  # un dimanche


def etat(eid, quand: datetime, s):
    return {"entity_id": eid, "state": s,
            "last_changed": quand.replace(tzinfo=ZoneInfo("Europe/Paris")).isoformat()}


def historique_type(jours=28, oubli_nuit=True):
    """Quart nuit un jour sur deux ; ces jours-là l'Inster n'est pas branchée à 21h."""
    series = {H.QUART: [], H.INSTER_LIEU: [], H.INSTER_PRISE: [], H.MAEL: [], "light.salon": [], H.NUIT: [], H.TV: []}
    for k in range(jours, 0, -1):
        j = AUJ - timedelta(days=k)
        d0 = datetime.combine(j, datetime.min.time())
        nuit = k % 2 == 0
        series[H.QUART].append(etat(H.QUART, d0 + timedelta(hours=1), "nuit" if nuit else "repos"))
        series[H.INSTER_LIEU].append(etat(H.INSTER_LIEU, d0 + timedelta(hours=18), "home"))
        series[H.INSTER_PRISE].append(etat(H.INSTER_PRISE, d0 + timedelta(hours=18, minutes=k),
                                           "off" if (nuit and oubli_nuit) else "on"))
        series[H.INSTER_PRISE].append(etat(H.INSTER_PRISE, d0 + timedelta(hours=23), "off"))
        # Mael part à 19h10, lumière du salon éteinte 5 min avant (jamais d'oubli)
        series["light.salon"].append(etat("light.salon", d0 + timedelta(hours=18), "on"))
        series["light.salon"].append(etat("light.salon", d0 + timedelta(hours=19, minutes=5), "off"))
        series[H.MAEL].append(etat(H.MAEL, d0 + timedelta(hours=8), "home"))
        series[H.MAEL].append(etat(H.MAEL, d0 + timedelta(hours=19, minutes=10), "not_home"))
    noms = {e: e for e in series}
    m = Memoire(":memory:")
    m.ajouter("historique", lignes_historique(list(series.values()), noms, ZoneInfo("Europe/Paris")))
    return m


class FauxCore:
    def __init__(self, memoire, etats=None):
        self.memoire = memoire
        self.etats = etats or {}
        self.appels = []

    async def etat(self, e):
        return self.etats.get(e, "on" if e == H.INTERRUPTEUR else None)

    async def appel(self, m, chemin, d=None, delai=60):
        self.appels.append((chemin, d))
        if chemin == "/states":
            return [{"entity_id": k, "state": v, "attributes": {}} for k, v in self.etats.items()]
        return {}


def test_lire_lignes_et_etat_a():
    h = H.lire_lignes([("14/11 21:05 Prise : allumé", "binary_sensor.p", "2026-11-14"),
                       ("pas une ligne", "x", "2026-11-14")])
    assert h == {"binary_sensor.p": [(datetime(2026, 11, 14, 21, 5), "on")]}
    assert H.etat_a(h["binary_sensor.p"], datetime(2026, 11, 14, 21, 0)) is None


def test_candidat_inster_quart_de_nuit():
    a = H.AgentHabitudes(FauxCore(historique_type()), "notify.x")
    c = a.calculer(AUJ)
    assert [x["regle"] for x in c] == ["inster"]
    assert c[0]["cle"] in ("quart:nuit",) or c[0]["cle"].startswith("jour+quart")
    assert c[0]["statut"] == "à proposer" and c[0]["oublis"] >= 3
    assert "21 h" in H.message(c[0]) and "D'accord" in H.message(c[0])


def test_aucun_candidat_sans_oubli():
    assert H.AgentHabitudes(FauxCore(historique_type(oubli_nuit=False)), "n.x").calculer(AUJ) == []


def test_proposition_puis_non_jamais_repropose(monkeypatch):
    monkeypatch.setattr(H, "maintenant_local", lambda: datetime(2026, 12, 20, 17, 30))
    core = FauxCore(historique_type())
    a = H.AgentHabitudes(core, "notify.x")
    r = asyncio.run(a.proposer())
    assert r["proposees"] == 1
    action = core.appels[0][1]["data"]["actions"][1]["action"]
    assert action.endswith("_NON")
    assert H.AgentHabitudes(core, "notify.x").recevoir_action({"action": action}) == "NON"  # après redémarrage
    core.appels.clear()
    assert asyncio.run(a.proposer())["proposees"] == 0 and a.derniers[0]["statut"] == "refusé"


def test_essai_et_interrupteur(monkeypatch):
    monkeypatch.setattr(H, "maintenant_local", lambda: datetime(2026, 12, 20, 17, 30))
    core = FauxCore(historique_type())
    assert asyncio.run(H.AgentHabitudes(core, "n.x").proposer(essai=True))["proposees"] == 0
    core.etats[H.INTERRUPTEUR] = "off"
    assert asyncio.run(H.AgentHabitudes(core, "n.x").proposer())["proposees"] == 0
    assert not [c for c in core.appels if c[0].startswith("/services/")]


def test_oui_cree_un_rappel_qui_ne_part_que_si_besoin(monkeypatch):
    monkeypatch.setattr(H, "maintenant_local", lambda: datetime(2026, 12, 20, 17, 30))
    core = FauxCore(historique_type())
    a = H.AgentHabitudes(core, "notify.x")
    asyncio.run(a.proposer())
    oui = core.appels[-2][1]["data"]["actions"][0]["action"] if False else \
        [x for x in core.appels if x[0].startswith("/services/notify")][0][1]["data"]["actions"][0]["action"]
    assert a.recevoir_action({"action": oui}) == "OUI" and a.rappels[0]["actif"]
    cle = a.rappels[0]["cle"]
    quart = cle.split("|")[-1].split(":")[-1]
    jour = AUJ + timedelta(days=1)
    while not H.situation_actuelle(cle, quart, datetime.combine(jour, datetime.min.time())):
        jour += timedelta(days=1)
    core.etats.update({H.QUART: quart, H.INSTER_LIEU: "home", H.INSTER_PRISE: "on"})
    core.appels.clear()
    t = datetime.combine(jour, datetime.min.time()) + timedelta(hours=20, minutes=45)
    assert asyncio.run(a.minute(t)) == []  # branchée : rien
    core.etats[H.INSTER_PRISE] = "off"
    assert asyncio.run(a.minute(t)) == ["inster"]
    assert asyncio.run(a.minute(t)) == []  # une fois par jour
    stop = core.appels[-1][1]["data"]["actions"][0]["action"]
    assert a.recevoir_action({"action": stop}) == "STOP" and not a.rappels[0]["actif"]
