import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import budget as B  # noqa: E402
import devoirs as D  # noqa: E402
import general as G  # noqa: E402
import menus as M  # noqa: E402
from store import Memoire  # noqa: E402

J = date(2026, 11, 18)


class FauxRouteur:
    def __init__(self, reponses):
        self.reponses = list(reponses)
        self.prompts = []

    async def demander(self, prompt, profondeur="rapide", tache="adria", *a, **k):
        self.prompts.append(prompt)
        r = self.reponses.pop(0)
        return {"ok": r is not None, "data": r or "", "moteur": "gemini"}


class FauxCore:
    def __init__(self, tmp_path, reponses=(), etats=None):
        self.memoire = Memoire(str(tmp_path / "c.db"))
        self.routeur = FauxRouteur(reponses)
        self.etats = etats or {}
        self.appels = []

    async def etat(self, e):
        return self.etats.get(e, "on")

    async def appel(self, methode, chemin, donnees=None, delai=60):
        self.appels.append((chemin, donnees))
        if "todo/get_items" in chemin:
            return {"service_response": {donnees["entity_id"]: {"items": [{"summary": "Lait"}]}}}
        if "calendar/get_events" in chemin:
            return {"service_response": {donnees["entity_id"]: {"events": []}}}
        if chemin == "/states":
            return []
        return {}


def notifs(core):
    return [d for c, d in core.appels if c.startswith("/services/notify")]


# ------------------------------------------------------------------------------- B1 budget
def test_analyser_depense():
    d = B.analyser_depense({"montant": "45,30 €", "magasin": "Leclerc", "categorie": "courses",
                            "date": "2026-11-17"}, J)
    assert d == {"jour": date(2026, 11, 17), "montant": 45.3, "categorie": "courses", "magasin": "Leclerc"}
    assert B.analyser_depense({"montant": "12", "magasin": "Pharmacie du port", "categorie": "?"}, J)["categorie"] == "sante"
    assert B.analyser_depense({"montant": "0"}, J) is None
    assert B.analyser_depense(None, J) is None
    assert B.analyser_depense({"montant": 10, "date": "2030-01-01"}, J)["jour"] == J  # date future -> aujourd'hui


def test_depense_vocale():
    d = B.depense_vocale("J'ai dépensé 45 euros 30 de courses chez Leclerc", J)
    assert d["montant"] == 45.3 and d["categorie"] == "courses" and d["magasin"] == "Leclerc"
    d = B.depense_vocale("on a payé 60€ d'essence hier", J)
    assert d["montant"] == 60 and d["categorie"] == "carburant" and d["jour"] == J - timedelta(days=1)
    assert B.depense_vocale("combien on a dépensé ce mois-ci ?", J) is None
    assert B.depense_vocale("il fait 12 degrés", J) is None


def test_totaux_alertes_bilan():
    dep = [{"jour": date(2026, m, 5), "montant": 300.0, "categorie": "courses", "magasin": ""} for m in (8, 9, 10)]
    dep += [{"jour": date(2026, 11, 3), "montant": 420.0, "categorie": "courses", "magasin": ""},
            {"jour": date(2026, 11, 4), "montant": 20.0, "categorie": "loisirs", "magasin": ""}]
    assert B.totaux(dep, 2026, 11) == {"courses": 420.0, "loisirs": 20.0}
    assert B.moyenne_3_mois(dep, 2026, 11) == {"courses": 300.0}
    al = B.alertes(dep, J, set())
    assert len(al) == 1 and al[0][0] == "2026-11-courses" and "+40 %" in al[0][1]
    assert B.alertes(dep, J, {"2026-11-courses"}) == []
    b = B.bilan(dep, 80.0, 2026, 11)
    assert "Total novembre 2026 : 520 €" in b and "abonnements : 80 €" in b and "+140 €" in b


def test_repondre_et_periode():
    dep = [{"jour": date(2026, 10, 5), "montant": 50.0, "categorie": "courses", "magasin": ""},
           {"jour": date(2026, 11, 5), "montant": 30.0, "categorie": "courses", "magasin": ""}]
    assert B.periode_question("le mois dernier", J) == (2026, 10)
    assert B.periode_question("en décembre", J) == (2025, 12)
    assert "30 €" in B.repondre("combien en courses ce mois-ci ?", dep, 0, J)
    assert "50 €" in B.repondre("et en courses le mois dernier ?", dep, 0, J)
    assert "aucun ticket de santé" in B.repondre("combien en pharmacie ?", dep, 0, J)


def test_budget_stockage_sans_doublon(tmp_path):
    mem = Memoire(str(tmp_path / "b.db"))
    bud = B.Budget(mem)
    d = {"jour": J, "montant": 12.5, "categorie": "courses", "magasin": "Lidl"}
    assert bud.ajouter(d, "Foyer", "courrier") is True
    assert bud.ajouter(d, "Foyer", "courrier") is False
    e = bud.etat(40.0, J)
    assert e["state"] == 52.5 and e["attributes"]["par_categorie"] == {"courses": 12.5}
    assert mem.chercher("dépense courses Lidl")


# ------------------------------------------------------------------------------- M1 menus
def test_menus_analyser_et_repondre():
    jours = M.jours_a_venir(date(2026, 11, 23), 7)
    brut = {"jours": [{"date": j.isoformat(), "plat": f"Plat {k}", "rapide": k == 1} for k, j in enumerate(jours)]
            + [{"date": "2030-01-01", "plat": "intrus"}], "courses": ["1 kg de carottes", "1 kg de carottes", "Lait"]}
    m = M.analyser(brut, jours)
    assert len(m["jours"]) == 7 and m["courses"] == ["1 kg de carottes", "Lait"]
    assert m["jours"]["2026-11-24"] == ("Plat 1", True)
    assert M.analyser({"jours": [{"date": "2026-11-23", "plat": "x"}]}, jours) is None
    assert M.a_ajouter(["Lait", "Œufs"], ["lait"]) == ["Œufs"]
    plats = {"2026-11-18": "Gratin", "2026-11-19": "Soupe"}
    assert M.repondre("qu'est-ce qu'on mange ce soir ?", plats, J) == "Ce soir, c'est Gratin."
    assert M.repondre("et demain ?", plats, J) == "Demain, c'est Soupe."
    assert "pas encore de menu" in M.repondre("samedi ?", plats, J)


def test_soirs_charges():
    jours = M.jours_a_venir(date(2026, 11, 23), 2)
    ev = [{"start": "2026-11-23T18:00:00+01:00", "summary": "Foot Ryan"},
          {"start": "2026-11-24T09:00:00+01:00", "summary": "Dentiste"}]
    assert M.soirs_charges(ev, jours) == {"2026-11-23": ["Foot Ryan"], "2026-11-24": []}


def test_menus_proposer_puis_oui(tmp_path, monkeypatch):
    class D_(date):
        @classmethod
        def today(cls):
            return date(2026, 11, 22)  # dimanche
    monkeypatch.setattr(M, "date", D_)
    jours = M.jours_a_venir(date(2026, 11, 23), 7)
    rep = json.dumps({"jours": [{"date": j.isoformat(), "plat": f"Plat {k}"} for k, j in enumerate(jours)],
                      "courses": ["Lait", "Pâtes"]})
    core = FauxCore(tmp_path, [rep])
    ag = M.AgentMenus(core, "notify.mobile_app_x")
    core.menus = ag
    r = asyncio.run(ag.proposer())
    assert r["ok"] and "Plat 0" in notifs(core)[0]["message"]
    action = notifs(core)[0]["data"]["actions"][0]["action"]
    assert asyncio.run(ag.recevoir_action({"action": action})) == "OUI"
    ajouts = [d["item"] for c, d in core.appels if c == "/services/todo/add_item"]
    assert ajouts == ["Pâtes"]  # le lait est déjà sur la liste
    assert ag.plats()["2026-11-23"] == "Plat 0"
    assert asyncio.run(ag.recevoir_action({"action": action})) is None  # pas deux fois


# ------------------------------------------------------------------------------- N1 devoirs
def test_devoirs_seance(tmp_path):
    core = FauxCore(tmp_path, ["Super Aelia ! Quel son fait la lettre m ?", "Bravo, c'est juste !"])
    ag = D.AgentDevoirs(core, "notify.mobile_app_x")
    m = datetime(2026, 11, 18, 17, 0)
    r = asyncio.run(ag.aider({"question": "aide-moi à lire maman", "qui": "Aelia (voix 90%)"}, m))
    assert r["ok"] and "lettre m" in r["texte"]
    assert "JAMAIS la reponse finale" in core.routeur.prompts[0] and "CP" in core.routeur.prompts[0]
    assert "aelia" in ag.sessions(m + timedelta(minutes=5))
    asyncio.run(ag.aider({"question": "mmmm", "qui": "aelia"}, m + timedelta(minutes=2)))
    assert "DEBUT DE LA SEANCE" in core.routeur.prompts[1]
    assert ag.sessions(m + timedelta(minutes=30)) == {}
    r = asyncio.run(ag.aider({"question": "x", "qui": "inconnu"}, m))
    assert not r["ok"]


def test_devoirs_bilan(tmp_path, monkeypatch):
    core = FauxCore(tmp_path, ["ok"])
    ag = D.AgentDevoirs(core, "notify.mobile_app_x")
    asyncio.run(ag.aider({"question": "les fractions", "qui": "iwan"}))
    asyncio.run(ag.bilan_soir())
    assert "Iwan : 1 question(s) — les fractions" in notifs(core)[-1]["message"]


# ------------------------------------------------------------------------------- G1 général
def test_verifier_proposition():
    m = datetime(2026, 11, 18, 17, 0)
    assert G.verifier_proposition({"type": "courses", "articles": ["Farine", " "]}, m) == \
        {"type": "courses", "articles": ["Farine"]}
    assert G.verifier_proposition({"type": "rappel", "quand": "2026-11-19 08:00", "texte": "Appeler"}, m)["quand"] \
        == "2026-11-19T08:00"
    assert G.verifier_proposition({"type": "rappel", "quand": "2026-11-01 08:00", "texte": "x"}, m) is None
    a = G.verifier_proposition({"type": "agenda", "debut": "2026-11-20 10:00", "titre": "Garage"}, m)
    assert a["fin"] == "2026-11-20T11:00"
    assert G.verifier_proposition({"type": "memo", "pour": "iwan", "texte": "Sors la poubelle"}, m)["pour"] == "iwan"
    assert G.verifier_proposition({"type": "memo", "pour": "voisin", "texte": "x"}, m) is None
    assert G.verifier_proposition({"type": "alarme"}, m) is None


def _core_general(tmp_path, reponses):
    core = FauxCore(tmp_path, reponses)
    core.menus = M.AgentMenus(core, "notify.mobile_app_x")
    core.budget = B.Budget(core.memoire)

    class S_:
        def etat(self):
            return {"state": 0, "attributes": {"abonnements": []}}
    core.suivis = S_()

    class C_:
        calendrier = "calendar.mael_adria"
    core.courriers = C_()
    return core


def test_general_reponse_proposition_oui(tmp_path):
    rep = json.dumps({"reponse_orale": "Pour une pâte à crêpes il faut farine, oeufs et lait.",
                      "reponse_detaillee": "Recette : 250 g de farine, 4 oeufs, 50 cl de lait... " * 3,
                      "sources": "connaissances générales",
                      "proposition": {"type": "courses", "articles": ["Farine", "Oeufs"]}})
    core = _core_general(tmp_path, [rep])
    ag = G.AgentGeneral(core, "notify.mobile_app_x")
    r = asyncio.run(ag.repondre({"question": "aide-moi à faire des crêpes", "qui": "Mael"}))
    assert r["ok"] and r["texte"].startswith("Pour une pâte") and "proposition" in r
    n = notifs(core)
    assert n[0]["title"] == "🧠 ADRIA propose" and len(n) == 2  # proposition + réponse détaillée
    act = n[0]["data"]["actions"][0]["action"]
    assert asyncio.run(ag.recevoir_action({"action": act})) == "OUI"
    assert [d["item"] for c, d in core.appels if c == "/services/todo/add_item"] == ["Farine", "Oeufs"]


def test_general_enfant_sans_proposition_ni_budget(tmp_path):
    rep = json.dumps({"reponse_orale": "Les dinosaures ont disparu il y a 66 millions d'années.",
                      "proposition": {"type": "courses", "articles": ["Bonbons"]}})
    core = _core_general(tmp_path, [rep])
    ag = G.AgentGeneral(core, "notify.mobile_app_x")
    r = asyncio.run(ag.repondre({"question": "pourquoi les dinosaures ont disparu", "qui": "iwan"}))
    assert r["ok"] and "proposition" not in r and notifs(core) == []
    assert "un enfant" in core.routeur.prompts[0] and "BUDGET" not in core.routeur.prompts[0]


def test_general_inconnu_limite_et_rappel(tmp_path):
    core = _core_general(tmp_path, [])
    ag = G.AgentGeneral(core, "notify.mobile_app_x")
    assert not asyncio.run(ag.repondre({"question": "x", "qui": "Quelqu'un"}))["ok"]
    m = datetime(2026, 11, 18, 9, 0)
    for _ in range(G.MAX_JOUR):
        assert ag.compter(m)
    assert not ag.compter(m)
    ag._ecrire("general_rappels", [{"quand": "2026-11-18T09:30", "texte": "Appeler l'école", "fait": False}])
    assert asyncio.run(ag.minute(datetime(2026, 11, 18, 9, 29))) == 0
    assert asyncio.run(ag.minute(datetime(2026, 11, 18, 9, 30))) == 1
    assert asyncio.run(ag.minute(datetime(2026, 11, 18, 9, 31))) == 0


def test_general_voix_chaine_interactive_et_secours(tmp_path, monkeypatch):
    rep = json.dumps({"reponse_orale": "Paris est la capitale de la France."})
    core = _core_general(tmp_path, [rep])
    core.prof = []
    vrai = core.routeur.demander

    async def espion(prompt, profondeur="rapide", tache="adria", *a, **k):
        core.prof.append(profondeur)
        return await vrai(prompt, profondeur, tache)
    core.routeur.demander = espion
    monkeypatch.setattr(G, "SECOURS_ATTENTE", 0)
    ag = G.AgentGeneral(core, "notify.mobile_app_x")
    asyncio.run(ag.secours({"question": "capitale de la France", "qui": "vanessa", "voix": "True",
                            "echo": "media_player.echo_salon", "asynchrone": True}))
    assert core.prof == ["interactif"]
    dit = [d for c, d in core.appels if c == "/services/script/adria_dire"]
    assert dit and dit[0]["message"].startswith("J'ai cherché plus loin. Paris")


def test_general_telephone_en_arriere_plan(tmp_path):
    core = _core_general(tmp_path, [json.dumps({"reponse_orale": "Voici ta lettre courte."}), None])
    ag = G.AgentGeneral(core, "notify.mobile_app_x")
    asyncio.run(ag.secours({"question": "rédige une lettre", "qui": "mael", "mode": "telephone"}))
    n = notifs(core)
    assert len(n) == 1 and n[0]["message"] == "Voici ta lettre courte."
    asyncio.run(ag.secours({"question": "x", "qui": "mael", "mode": "telephone"}))  # IA en panne
    assert "n'arrive pas" in notifs(core)[-1]["message"]
