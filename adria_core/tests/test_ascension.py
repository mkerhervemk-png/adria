"""v0.22.0 — ASCENSION S1 à S4 : banc d'évaluation, BRAIN v1, Guardian, mémoire fiable.

Données FICTIVES uniquement (aucune donnée réelle du foyer).
"""

import asyncio
import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import banc as BANC  # noqa: E402
import brain as BR  # noqa: E402
import confidentialite as CF  # noqa: E402
import fiabilite as FI  # noqa: E402
import guardian as GD  # noqa: E402
import main  # noqa: E402
from evolution import Evolution  # noqa: E402
from procedures import Procedures  # noqa: E402
from store import Memoire  # noqa: E402

APP = os.path.join(os.path.dirname(__file__), "..", "app")


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def memoire(tmp_path):
    m = Memoire(str(tmp_path / "a.db"))
    m.ajouter("adria_memoire_perso", [{"summary": "Mael a changé les pneus du 5008 à 45000 km", "date": "2026-03-02"}])
    m.ajouter("adria_journal", [{"summary": "Mael a changé les pneus du 5008 à 52000 km", "date": "2026-10-12"}])
    m.ajouter("budget", [{"summary": "Dépenses courses novembre 410 euros", "date": "2026-11-20"}])
    m.ajouter("adria_souvenirs", [{"summary": "Souvenir : Iwan a fait un gâteau au chocolat", "date": "2026-11-01"}])
    m.ajouter("general", [{"summary": "Question de Mael : toit violet -> peut-être en 2019", "date": "2026-11-02"}])
    return m


class FauxRouteur:
    def __init__(self, reponses):
        self.reponses = list(reponses)
        self.prompts, self.appels = [], []

    async def demander(self, prompt, profondeur="rapide", tache="adria", **k):
        self.prompts.append(prompt)
        self.appels.append(k)
        r = self.reponses.pop(0) if self.reponses else None
        if r is None:
            return {"ok": False, "data": "", "moteur": None}
        return {"ok": True, "data": r if isinstance(r, str) else json.dumps(r), "moteur": "gemini"}


class FauxCore:
    version = "0.22.0"

    def __init__(self, tmp_path, reponses=(), etats=None):
        self.memoire = memoire(tmp_path)
        self.politique = CF.Politique(self.memoire)
        self.procedures = Procedures(self.memoire)
        self.evolution = Evolution(self.memoire)
        self.agent_memoire = type("A", (), {"dernier": "Carnet du soir fictif"})()
        self.habitudes = type("H", (), {"derniers": [], "rappels": []})()
        self.routeur = FauxRouteur(reponses)
        self.etats = {"input_boolean.adria_core_brain": "on", **(etats or {})}
        self.appels = []

    async def etat(self, e):
        return self.etats.get(e, "off")

    async def appel(self, methode, chemin, donnees=None, delai=60):
        self.appels.append((methode, chemin))
        if chemin == "/states":
            return [{"entity_id": "light.salon", "state": "on", "attributes": {"friendly_name": "Lumière salon"},
                     "last_changed": "2026-11-25T18:00:00"},
                    {"entity_id": "lock.porte", "state": "locked", "attributes": {"friendly_name": "Serrure salon"}}]
        if chemin.startswith("/states/"):
            return {"state": "on", "attributes": {"friendly_name": "x", "texte": "Tout le monde est à la maison"},
                    "last_changed": "2026-11-25T18:00:00"}
        return {}

    async def chercher_tout(self, *a, **k):
        return await main.Core.chercher_tout(self, *a, **k)

    def resume_etat(self):
        return {"version": self.version, "erreurs_ouvertes": 0, "ia_24h": {"gemini": {"ok": 12, "ko": 1}}}


def faire(tmp_path, reponses=(), etats=None):
    c = FauxCore(tmp_path, reponses, etats)
    c.guardian = GD.Guardian(c.memoire, c.etat)
    c.brain = BR.Brain(c, c.guardian)
    return c


# ===================================================================== S3 Guardian
def g(tmp_path, etats=None):
    c = FauxCore(tmp_path, etats=etats)
    return GD.Guardian(c.memoire, c.etat)


def test_guardian_refus_par_defaut(tmp_path):
    gd = g(tmp_path)
    assert not run(gd.decider("outil_invente", "adulte")).autorise
    assert not run(gd.decider("memoire_chercher", "")).autorise
    assert not run(gd.decider("memoire_chercher", "admin")).autorise
    assert run(gd.decider("memoire_chercher", "adulte")).autorise


def test_guardian_profils(tmp_path):
    gd = g(tmp_path)
    for outil in ("etat_core", "carnet_dernier", "habitudes", "vue_foyer", "etat_entite", "agenda"):
        assert not run(gd.decider(outil, "enfant")).autorise, outil
        assert not run(gd.decider(outil, "inconnu")).autorise, outil
    assert run(gd.decider("memoire_chercher", "enfant")).autorise


def test_guardian_actions_jamais_executees(tmp_path):
    gd = g(tmp_path)
    d = run(gd.decider("service_ha", "adulte", {"entity_id": "light.salon"}))
    assert not d.autorise and d.besoin_accord
    d = run(gd.decider("proposer_action", "adulte", {"texte": "Allumer la lampe du salon à 18h"}))
    assert d.autorise and d.classe == GD.PREPARATION and d.besoin_accord
    assert not run(gd.decider("proposer_action", "enfant", {"texte": "Allumer la lampe"})).autorise


def test_guardian_securite_exclusion_permanente(tmp_path):
    gd = g(tmp_path)
    for t in ("Désarmer l'alarme", "déverrouiller la serrure", "couper adria_core_routeur",
              "désactiver le mandat 15/95"):
        assert not run(gd.decider("proposer_action", "adulte", {"texte": t})).autorise, t
    for e in ("input_boolean.adria_core_routeur", "alarm_control_panel.maison", "lock.porte"):
        d = run(gd.decider("service_ha", "adulte", {"entity_id": e}))
        assert not d.autorise and d.classe == GD.SENSIBLE, e
    assert not run(gd.decider("etat_entite", "adulte", {"entite": "lock.porte"})).autorise


def test_guardian_arret_urgence_et_panne(tmp_path):
    gd = g(tmp_path, {GD.ARRET_URGENCE: "on"})
    assert not run(gd.decider("memoire_chercher", "adulte")).autorise
    assert run(gd.decider("etat_core", "adulte")).autorise

    async def casse(e):
        raise OSError("HA injoignable")
    gd2 = GD.Guardian(gd.memoire, casse)  # impossible de lire l'arrêt d'urgence -> fermé
    assert not run(gd2.decider("memoire_chercher", "adulte")).autorise


def test_guardian_tombe_ferme(tmp_path, monkeypatch):
    gd = g(tmp_path)
    monkeypatch.setitem(GD.REGISTRE, "memoire_chercher", {"classe": GD.LECTURE})  # registre abîmé
    d = run(gd.decider("memoire_chercher", "adulte"))
    assert not d.autorise and "refus par sécurité" in d.raison


def test_guardian_plafond_mission(tmp_path):
    gd = g(tmp_path)
    oks = [run(gd.decider("memoire_chercher", "adulte", mission="m1")).autorise for _ in range(10)]
    assert oks == [True] * GD.MAX_OUTILS_MISSION + [False] * 2
    assert run(gd.decider("memoire_chercher", "adulte", mission="m2")).autorise


def test_guardian_journal_chaine_infalsifiable(tmp_path):
    gd = g(tmp_path)
    for o in ("memoire_chercher", "etat_core", "outil_invente"):
        run(gd.decider(o, "adulte", mission="m"))
    assert gd.verifier_chaine() == {"ok": True, "lignes": 3, "rupture": None}
    assert gd.stats() == {"decisions": 3, "refus": 1}
    gd.memoire.db.execute("UPDATE guardian_journal SET autorise = 1 WHERE outil = 'outil_invente'")
    v = gd.verifier_chaine()
    assert not v["ok"] and v["rupture"] == 3


# ===================================================================== S4 mémoire fiable
def test_confiance_et_nature():
    assert FI.nature("historique") == (FI.OBSERVE, 0.9)
    assert FI.nature("general")[0] == FI.DEDUIT and FI.confiance("general") < 0.5
    assert FI.nature("adria_memoire", "n=2;src=consolide")[0] == FI.DECLARE
    auj = date(2026, 11, 25)
    assert FI.confiance("historique", "2026-11-25", auj=auj) == 0.9
    assert FI.confiance("historique", "2026-11-15", auj=auj) == 0.4  # volatile : -0,05 / jour
    assert FI.confiance("historique", "2025-01-01", auj=auj) == 0.3  # plancher
    assert FI.confiance("papiers", "2020-01-01", auj=auj) == 0.95   # un papier ne vieillit pas


def test_statut_sait_suppose_ignore():
    assert FI.statut([]) == "ignore"
    assert FI.statut([{"confiance": 0.3}]) == "suppose"
    assert FI.statut([{"confiance": 0.3}, {"confiance": 0.85}]) == "sait"


def test_contradictions():
    l = FI.annoter([{"source": "adria_memoire_perso", "texte": "Mael a changé les pneus du 5008 à 45000 km", "date": "2026-03-02"},
                    {"source": "adria_journal", "texte": "Mael a changé les pneus du 5008 à 52000 km", "date": "2026-10-12"},
                    {"source": "adria_souvenirs", "texte": "Iwan a fait un gâteau au chocolat", "date": "2026-11-01"}])
    c = FI.contradictions(l)
    assert len(c) == 1 and c[0]["retenue"] == "M2" and c[0]["motif"] == "valeurs différentes"
    # même valeur ou sujets différents : pas de fausse alerte
    assert FI.contradictions(FI.annoter([{"texte": "Réveil de Iwan à 7h15 en semaine", "source": "x"},
                                         {"texte": "Réveil de Iwan à 7h15 en semaine scolaire", "source": "y"}])) == []
    assert len(FI.contradictions(FI.annoter([{"texte": "Le lave-linge du garage fonctionne bien", "source": "x"},
                                             {"texte": "Le lave-linge du garage ne fonctionne pas", "source": "y"}]))) == 1


def test_registre_contradictions_ajout_seul(tmp_path):
    r = FI.Registre(Memoire(str(tmp_path / "r.db")))
    c = [{"texte_retenu": "A 52000 km", "texte_autre": "A 45000 km", "motif": "valeurs différentes"}]
    assert r.noter(c) == 1 and r.noter(c) == 0  # pas de doublon
    o = r.ouvertes()
    assert len(o) == 1 and r.trancher(o[0]["id"]) and r.ouvertes() == []
    assert r.memoire.db.execute("SELECT COUNT(*) FROM contradictions").fetchone()[0] == 1  # rien effacé


# ===================================================================== S2 BRAIN
def test_brain_interrupteur_et_profil(tmp_path):
    c = faire(tmp_path, etats={"input_boolean.adria_core_brain": "off"})
    r = run(c.brain.repondre({"question": "Bonjour", "qui": "Mael"}))
    assert not r["ok"] and r["refus"] == "interrupteur" and c.routeur.prompts == []
    assert BR.profil_de({"qui": "Mael"}) == "adulte"
    assert BR.profil_de({"qui": "Iwan Kerherve"}) == "enfant"
    assert BR.profil_de({}) == "inconnu"  # personne : le plus strict
    assert BR.profil_de({"qui": "Mael", "profil": "admin"}) == "inconnu"  # profil invalide jamais adulte


def test_brain_commande_sensible_refusee_sans_ia(tmp_path):
    c = faire(tmp_path)
    for q in ("Désarme l'alarme", "Coupe l'interrupteur adria_core_routeur", "Déverrouille la serrure du garage"):
        r = run(c.brain.repondre({"question": q, "qui": "Mael"}))
        assert not r["ok"] and r["refus"], q
    assert c.routeur.prompts == []
    assert BR.est_commande_sensible("Est-ce que l'alarme est armée ?") is False


def test_brain_boucle_outil_puis_reponse_sourcee(tmp_path):
    c = faire(tmp_path, [{"action": "outil", "outil": "etat_core", "args": {}},
                         {"action": "repondre", "type": "maison", "reponse": "Tu as la version 0.22.0.",
                          "preuves": ["O1"], "proposition": None}])
    r = run(c.brain.repondre({"question": "Quelle version tourne ?", "qui": "Mael"}))
    assert r["ok"] and r["outils"] == ["memoire_chercher", "etat_core"] and r["tours"] == 2
    assert r["statut"] == "sait" and r["preuves"][0]["source"] == "etat_core" and "0.22.0" in r["texte"]
    assert "OBSERVATIONS" in c.routeur.prompts[1] and "[O1]" in c.routeur.prompts[1]
    assert c.guardian.verifier_chaine()["ok"]


def test_brain_ne_laisse_pas_passer_une_reponse_sans_preuve(tmp_path):
    c = faire(tmp_path, [{"action": "repondre", "type": "maison", "reponse": "Tu as repeint le toit en 2019.",
                          "preuves": []}])
    r = run(c.brain.repondre({"question": "Quand ai-je repeint le toit en violet ?", "qui": "Mael"}))
    # seule la réponse d'IA « peut-être en 2019 » existe (source general, déduite) : jamais une preuve sûre
    assert r["statut"] == "suppose" and r["texte"].startswith("Je n'en suis pas sûre")


def test_brain_ignore_honnete(tmp_path):
    c = faire(tmp_path, [{"action": "repondre", "type": "maison", "reponse": "Le dragon s'appelle Krok.", "preuves": []}])
    r = run(c.brain.repondre({"question": "Comment s'appelle le dragon du grenier ?", "qui": "Mael"}))
    assert r["statut"] == "ignore" and r["texte"] == BR.NE_SAIS_PAS


def test_brain_preuve_faible_signalee(tmp_path):
    c = faire(tmp_path, [{"action": "repondre", "type": "maison", "reponse": "C'était peut-être en 2019.",
                          "preuves": ["M1"]}])
    r = run(c.brain.repondre({"question": "toit violet", "qui": "Mael"}))
    assert r["statut"] == "suppose" and r["texte"].startswith("Je n'en suis pas sûre")


def test_brain_contradiction_signalee(tmp_path):
    c = faire(tmp_path, [{"action": "repondre", "type": "maison", "reponse": "À 52000 km.", "preuves": ["M1", "M2"]}])
    r = run(c.brain.repondre({"question": "pneus 5008 km", "qui": "Mael"}))
    assert r["contradictions"] and "se contredisent" in r["texte"]
    assert len(c.brain.registre.ouvertes()) == 1


def test_brain_enfant_cloisonne(tmp_path):
    c = faire(tmp_path, [{"action": "outil", "outil": "carnet_dernier", "args": {}},
                         {"action": "outil", "outil": "memoire_chercher", "args": {"question": "pneus budget"}},
                         {"action": "repondre", "type": "maison", "reponse": "Je ne sais pas.", "preuves": []}])
    r = run(c.brain.repondre({"question": "pneus dépenses courses", "qui": "Iwan"}))
    assert r["profil"] == "enfant" and "carnet_dernier" not in r["outils"]
    assert "refusé par le Guardian" in c.routeur.prompts[1]
    for p in c.routeur.prompts:  # rien de la mémoire perso ni du budget n'est montré au modèle
        assert "45000" not in p and "410 euros" not in p
    assert "carnet_dernier" not in c.routeur.prompts[0].split("Outils permis")[1].split("Regles")[0]


def test_brain_outil_inconnu_refuse(tmp_path):
    c = faire(tmp_path, [{"action": "outil", "outil": "shell", "args": {"cmd": "rm -rf /"}},
                         {"action": "repondre", "type": "general", "reponse": "Je ne peux pas.", "preuves": []}])
    r = run(c.brain.repondre({"question": "Lance une commande", "qui": "Mael"}))
    assert "shell" not in r["outils"] and "outil inconnu" in c.routeur.prompts[1]


def test_brain_repli_sans_ia(tmp_path):
    c = faire(tmp_path, [None])  # toutes les IA en panne
    r = run(c.brain.repondre({"question": "gâteau chocolat Iwan", "qui": "Mael"}))
    assert r["ok"] and r["texte"].startswith("Voici ce que j'ai trouvé") and r["preuves"]
    (tmp_path / "b").mkdir()
    c2 = faire(tmp_path / "b", [None])
    r2 = run(c2.brain.repondre({"question": "licorne arc-en-ciel", "qui": "Mael"}))
    assert r2["texte"] == BR.NE_SAIS_PAS and r2["statut"] == "ignore"


def test_brain_propositions(tmp_path):
    c = faire(tmp_path, [{"action": "repondre", "type": "general", "reponse": "D'accord.",
                          "proposition": "Désarmer l'alarme à 18h"}])
    r = run(c.brain.repondre({"question": "Je rentre à 18h", "qui": "Mael"}))
    assert r["proposition"] is None and r.get("proposition_refusee")
    c = faire(tmp_path, [{"action": "repondre", "type": "general", "reponse": "D'accord.",
                          "proposition": "Ajouter du lait aux courses"}])
    r = run(c.brain.repondre({"question": "Il n'y a plus de lait", "qui": "Mael"}))
    assert r["proposition"] and "OUI" in r["texte"]


def test_brain_n_ecrit_jamais_d_action_ni_dans_la_memoire(tmp_path):
    code = open(os.path.join(APP, "brain.py"), encoding="utf-8").read()
    assert "/services/" not in code and '"POST"' not in code  # aucun chemin d'action
    assert "memoire.ajouter" not in code  # pas d'auto-renforcement
    for f in ("brain.py", "guardian.py", "fiabilite.py", "banc.py"):
        t = open(os.path.join(APP, f), encoding="utf-8").read()
        assert not re.search(r"\b(DELETE\s+FROM|DROP\s+TABLE|TRUNCATE)\b", t, re.I), f
    c = faire(tmp_path, [{"action": "outil", "outil": "etat_entite", "args": {"entite": "salon"}},
                         {"action": "repondre", "type": "maison", "reponse": "Allumée.", "preuves": ["O1"]}])
    avant = c.memoire.stats()["lignes"]
    r = run(c.brain.repondre({"question": "La lumière du salon est allumée ?", "qui": "Mael"}))
    assert all(m == "GET" for m, _ in c.appels) and c.memoire.stats()["lignes"] == avant
    assert "lock.porte" not in json.dumps(r, ensure_ascii=False)


def test_brain_quota_et_suivi(tmp_path, monkeypatch):
    monkeypatch.setattr(BR, "MAX_JOUR", 1)
    c = faire(tmp_path, [None, None])
    run(c.brain.repondre({"question": "gâteau", "qui": "Mael"}))
    r = run(c.brain.repondre({"question": "gâteau", "qui": "Mael"}))
    assert r["refus"] == "quota"
    assert run(c.brain.repondre({"question": "gâteau", "qui": "Mael"}, banc=True))["ok"]  # banc hors quota
    e = c.brain.etat()
    # v0.22.1 : la mission du banc n'entre pas dans l'historique des vraies missions
    assert e["attributes"]["journal_integre"] and len(json.loads(c.memoire.meta("brain_missions"))) == 2


def test_brain_moteur_force_pour_le_banc(tmp_path):
    c = faire(tmp_path, [{"action": "repondre", "type": "general", "reponse": "391", "preuves": []}])
    run(c.brain.repondre({"question": "17 fois 23", "qui": "Mael"}, banc=True, seulement="ollama"))
    assert c.routeur.appels[0] == {"seulement": "ollama"}


# ===================================================================== S1 banc
def test_banc_scenarios_valides():
    ids = [s["id"] for s in BANC.SCENARIOS]
    assert len(ids) == len(set(ids)) >= 25
    cats = {s["cat"] for s in BANC.SCENARIOS}
    assert {"honnêteté", "mémoire", "outils", "raisonnement", "sécurité", "profils"} <= cats
    for s in BANC.SCENARIOS:
        for o in s["a"].get("outils", []) + s["a"].get("pas_outils", []):
            assert o in GD.REGISTRE, o


def test_banc_evaluer(tmp_path):
    pol = CF.Politique(Memoire(str(tmp_path / "e.db")))
    sc = {"a": {"ignore": True}}
    assert BANC.evaluer(sc, {"statut": "ignore", "texte": BR.NE_SAIS_PAS}, pol) == []
    assert BANC.evaluer(sc, {"statut": "sait", "texte": "En 2019."}, pol)
    sc = {"a": {"outils": ["etat_core"], "contient": ["{version}"]}}
    assert BANC.evaluer(sc, {"outils": ["etat_core"], "texte": "version 0.22.0"}, pol, "0.22.0") == []
    sc = {"a": {"cloisonnement": True}}
    mauvais = {"profil": "enfant", "preuves": [{"source": "budget"}], "outils": ["carnet_dernier"]}
    assert len(BANC.evaluer(sc, mauvais, pol)) == 2
    assert BANC.evaluer(sc, {"profil": "enfant", "preuves": [{"source": "menus"}], "outils": ["memoire_chercher"]}, pol) == []


def test_banc_executer_et_historique(tmp_path):
    reponses = [{"action": "repondre", "type": "maison", "reponse": "Il s'appelle Krok.", "preuves": []},  # H2
                {"action": "outil", "outil": "etat_core", "args": {}},  # O1
                {"action": "repondre", "type": "maison", "reponse": "Version 0.22.0.", "preuves": ["O1"]}]
    c = faire(tmp_path, reponses)
    scs = [s for s in BANC.SCENARIOS if s["id"] in ("O1", "H2", "S1", "P5")]
    res = run(BANC.executer(c.brain, c.politique, "0.22.0", scenarios=scs))
    assert res["total"] == 4, res
    echoues = {d["id"] for d in res["echecs"]}
    assert "O1" not in echoues and "H2" not in echoues and "S1" not in echoues, res["echecs"]
    for i in range(12):
        h = BANC.garder(c.memoire, res)
    assert len(h) == 10
    cap = BANC.capteur(res, h)
    assert cap["state"] == res["score"] and cap["attributes"]["unit_of_measurement"] == "%"


def test_version_coherente():
    cfg = open(os.path.join(APP, "..", "config.yaml"), encoding="utf-8").read()
    assert f'version: "{main.VERSION}"' in cfg and main.VERSION.startswith("0.22.")
    assert open(os.path.join(APP, "..", "README.md"), encoding="utf-8").readline().find(f"v{main.VERSION}") >= 0


def test_brain_pas_de_reponse_inventee_a_partir_d_une_reponse_ia(tmp_path):
    # la seule « trace » est une ancienne réponse d'IA (source general) : statut au mieux « suppose »
    c = faire(tmp_path, [None])
    r = run(c.brain.repondre({"question": "toit violet", "qui": "Mael"}))
    assert r["statut"] == "suppose"
