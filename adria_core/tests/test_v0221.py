"""v0.22.1 — correctif de l'audit de la v0.22.0 (Guardian, profils, preuves, banc). Données fictives."""

import asyncio
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.dirname(__file__))

import banc as BANC  # noqa: E402
import brain as BR  # noqa: E402
import guardian as GD  # noqa: E402
import main  # noqa: E402
import sens as SE  # noqa: E402
from store import Memoire  # noqa: E402
from test_ascension import FauxCore, faire  # noqa: E402

APP = os.path.join(os.path.dirname(__file__), "..", "app")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


def guardian(tmp_path, etats):
    c = FauxCore(tmp_path, etats=etats)

    async def etat(e):  # pas de valeur par défaut « off » : un état absent reste absent (None)
        return etats.get(e)
    return GD.Guardian(c.memoire, etat)


# ============================================================ 1. arrêt d'urgence fermé par défaut
def test_arret_urgence_illisible_ferme(tmp_path):
    for i, valeur in enumerate((None, "unknown", "unavailable", "", "on", "ON?")):
        (tmp_path / str(i)).mkdir()
        gd = guardian(tmp_path / str(i), {GD.ARRET_URGENCE: valeur})
        d = run(gd.decider("memoire_chercher", "adulte"))
        assert not d.autorise and "arrêt d'urgence" in d.raison, valeur
        assert run(gd.decider("etat_core", "adulte")).autorise  # seul l'état du Core reste lisible
    (tmp_path / "off").mkdir()
    gd = guardian(tmp_path / "off", {GD.ARRET_URGENCE: "off"})
    assert run(gd.decider("memoire_chercher", "adulte")).autorise


def test_brain_reste_muet_si_arret_illisible(tmp_path):
    c = faire(tmp_path, [{"action": "repondre", "type": "general", "reponse": "x"}],
              etats={GD.ARRET_URGENCE: "unavailable"})
    r = run(c.brain.repondre({"question": "Que s'est-il passé hier ?", "qui": "Mael"}))
    assert not r["ok"] and "arrêt d'urgence" in r["refus"] and c.routeur.prompts == []


# ============================================================ 2. journal obligatoire
class BaseEnPanne:
    """Enveloppe sqlite : toute écriture dans guardian_journal échoue."""

    def __init__(self, db):
        self.db = db

    def execute(self, sql, *a):
        if sql.lstrip().upper().startswith("INSERT INTO GUARDIAN_JOURNAL"):
            raise OSError("disque plein")
        return self.db.execute(sql, *a)

    def commit(self):
        return self.db.commit()


def test_echec_de_journalisation_refuse(tmp_path):
    gd = guardian(tmp_path, {GD.ARRET_URGENCE: "off"})
    gd.memoire.db = BaseEnPanne(gd.memoire.db)
    d = run(gd.decider("memoire_chercher", "adulte"))
    assert not d.autorise and "journal" in d.raison
    assert gd.refus == 1


# ============================================================ 3. filtre central des entités sensibles
SENSIBLES = ["input_boolean.adria_core_routeur", "input_boolean.adria_core_arret_urgence",
             "input_boolean.adria_claude_actif", "input_number.adria_seuil", "input_select.adria_mode",
             "input_boolean.mandat_15_95", "sensor.adria_core_ia", "lock.porte_entree",
             "alarm_control_panel.maison", "camera.cour", "siren.garage", "input_boolean.alarme_nuit"]


def test_interrupteurs_core_tous_proteges():
    """Tous les input_boolean.adria_core_* connus (v0.21.3 + nouveaux) sont refusés par le filtre central."""
    with open(os.path.join(FIXTURES, "interrupteurs_v0213.txt"), encoding="utf-8") as f:
        historiques = {l.strip() for l in f if l.strip()}
    actuels = set()
    for nom in os.listdir(APP):
        if nom.endswith(".py"):
            with open(os.path.join(APP, nom), encoding="utf-8") as f:
                actuels |= set(re.findall(r"input_boolean\.adria_core_[a-z_]+", f.read()))
    assert len(historiques) >= 20
    assert historiques <= actuels, f"interrupteurs disparus : {historiques - actuels}"
    for e in actuels | historiques:
        assert not GD.entite_autorisee(e), e


def test_filtre_central_tous_chemins(tmp_path):
    gd = guardian(tmp_path, {GD.ARRET_URGENCE: "off"})
    for e in SENSIBLES:
        assert not GD.entite_autorisee(e), e
        for outil, cle in (("etat_entite", "entite"), ("historique_entite", "entite"), ("service_ha", "entity_id")):
            d = run(gd.decider(outil, "adulte", {cle: e}))
            assert not d.autorise and d.classe == GD.SENSIBLE, (outil, e)
    for e in ("light.salon", "sensor.temperature_salon", "person.mael_kerherve", "sensor.adria_core"):
        assert GD.entite_autorisee(e), e
        assert run(gd.decider("etat_entite", "adulte", {"entite": e})).autorise, e
    assert not GD.entite_autorisee("light.salon; DROP") and not GD.entite_autorisee("")


def test_brain_deuxieme_barriere(tmp_path):
    c = faire(tmp_path)
    for e in SENSIBLES:
        assert run(c.brain._etat_entite(e)) == [], e
        assert run(c.brain._historique(e, 24)) == [], e
    assert not any("/states/lock" in ch or "adria_core_routeur" in ch for _, ch in c.appels)


class CoreAvecSensibles(FauxCore):
    async def appel(self, methode, chemin, donnees=None, delai=60):
        if chemin == "/states":
            self.appels.append((methode, chemin))
            return [{"entity_id": "input_boolean.adria_core_routeur", "state": "on",
                     "attributes": {"friendly_name": "Routeur salon"}},
                    {"entity_id": "camera.salon", "state": "idle", "attributes": {"friendly_name": "Caméra salon"}},
                    {"entity_id": "light.salon", "state": "on", "attributes": {"friendly_name": "Lumière salon"}}]
        return await super().appel(methode, chemin, donnees, delai)


def test_recherche_par_nom_filtre_central(tmp_path):
    c = CoreAvecSensibles(tmp_path)
    c.guardian = GD.Guardian(c.memoire, c.etat)
    c.brain = BR.Brain(c, c.guardian)
    ids = {r["entity_id"] for r in run(c.brain._etat_entite("salon"))}
    assert "light.salon" in ids and not ids & {"input_boolean.adria_core_routeur", "camera.salon"}


def test_aucune_ecriture_sur_les_interrupteurs():
    for nom in ("brain.py", "guardian.py", "fiabilite.py", "banc.py"):
        with open(os.path.join(APP, nom), encoding="utf-8") as f:
            code = f.read()
        assert not re.search(r"input_boolean/(turn_on|turn_off|toggle)|/services/", code), nom


# ============================================================ 4. profils : on peut restreindre, jamais élargir
def test_profil_jamais_eleve():
    assert BR.profil_de({"qui": "Mael", "profil": "enfant"}) == "enfant"
    assert BR.profil_de({"qui": "Mael", "profil": "inconnu"}) == "inconnu"
    assert BR.profil_de({"qui": "Iwan", "profil": "adulte"}) == "enfant"
    assert BR.profil_de({"profil": "adulte"}) == "inconnu"
    assert BR.profil_de({"qui": "Inconnu", "profil": "adulte"}) == "inconnu"
    assert BR.profil_de({"qui": "Mael", "profil": "admin"}) == "inconnu"
    assert BR.profil_de({"qui": "Mael", "profil": "ADULTE"}) == "adulte"


def test_profil_adulte_declare_sans_qui_cloisonne(tmp_path):
    c = faire(tmp_path, [{"action": "outil", "outil": "etat_core", "args": {}},
                         {"action": "repondre", "type": "maison", "reponse": "x", "preuves": []}])
    r = run(c.brain.repondre({"question": "pneus 5008", "profil": "adulte"}))
    assert r["profil"] == "inconnu" and "etat_core" not in r["outils"]
    assert all("45000" not in p for p in c.routeur.prompts)


# ============================================================ 5. preuves non pertinentes
def test_preuve_citee_hors_sujet(tmp_path):
    c = faire(tmp_path, [{"action": "outil", "outil": "etat_core", "args": {}},
                         {"action": "repondre", "type": "maison",
                          "reponse": "Iwan rentre de la piscine vers dix-huit heures.", "preuves": ["O1"]}])
    r = run(c.brain.repondre({"question": "Quand rentre Iwan ?", "qui": "Mael"}))
    assert r["preuves_ecartees"] == ["O1"] and r["preuves"] == []
    assert r["statut"] == "suppose" and r["texte"].startswith("Je n'en suis pas sûre")


def test_preuve_pertinente_conservee(tmp_path):
    c = faire(tmp_path, [{"action": "outil", "outil": "etat_core", "args": {}},
                         {"action": "repondre", "type": "maison", "reponse": "Tu as la version 0.22.0.",
                          "preuves": ["O1"]}])
    r = run(c.brain.repondre({"question": "Quelle version ?", "qui": "Mael"}))
    assert r["statut"] == "sait" and [p["ref"] for p in r["preuves"]] == ["O1"] and "preuves_ecartees" not in r


def test_pertinentes():
    p = [{"texte": "Mael a changé les pneus du 5008 à 52000 km"}, {"texte": "Météo pluvieuse"}]
    assert BR.pertinentes("Les pneus ont été changés à 52000 km.", p) == p[:1]
    assert BR.pertinentes("Oui.", p) == []


# ============================================================ 6. banc : quota persistant et sans effets de bord
def test_quota_banc_persistant(tmp_path):
    chemin = str(tmp_path / "q.db")
    m = Memoire(chemin)
    j = datetime(2026, 11, 25, 18, 0)
    assert BANC.reserver(m, j) and BANC.reserver(m, j) and not BANC.reserver(m, j)
    m2 = Memoire(chemin)  # redémarrage du Core : le compteur est dans la base
    assert not BANC.reserver(m2, j)
    assert BANC.reserver(m2, datetime(2026, 11, 26, 0, 1))


def test_lancer_banc_respecte_le_quota(tmp_path, monkeypatch):
    lances = []

    async def faux_executer(*a, **k):
        lances.append(1)
        return {"quand": "x", "version": "t", "moteur": "chaîne", "score": 100, "reussis": 1, "total": 1,
                "par_categorie": {}, "latence_moyenne": 0, "echecs": [], "details": []}
    monkeypatch.setattr(BANC, "executer", faux_executer)
    c = faire(tmp_path)
    c.banc_en_cours = False
    c.registre = None  # révision 6 : le quota ne dépend pas du registre
    publies = []

    async def publier(e, etat):
        publies.append(e)
    c.publier_capteur = publier
    for _ in range(4):
        run(main.Core.lancer_banc(c, {}))
    assert len(lances) == BANC.MAX_JOUR == 2 and not c.banc_en_cours


class SensEnPanne:
    async def chercher(self, *a, **k):
        raise TimeoutError()


def test_banc_sans_effet_de_bord(tmp_path):
    reponses = [{"action": "repondre", "type": "maison", "reponse": "À 52000 km.", "preuves": ["M1", "M2"]}]
    c = faire(tmp_path, reponses * 2, etats={SE.INTERRUPTEUR: "on"})
    c.sens = SensEnPanne()
    avant = c.evolution.lister(None, 500)  # le journal contient déjà des entrées d'origine
    r = run(c.brain.repondre({"question": "pneus 5008 km changé", "qui": "Mael"}, banc=True))
    assert r["contradictions"]  # la contradiction est vue…
    assert c.evolution.lister(None, 500) == avant  # …mais rien n'est écrit dans le journal des erreurs
    assert c.brain.registre.ouvertes() == [] and c.memoire.meta("brain_missions") is None
    # contrôle : la même mission hors banc écrit bien (le test sait détecter un effet de bord)
    run(c.brain.repondre({"question": "pneus 5008 km changé", "qui": "Mael"}))
    assert c.evolution.lister(None, 500) != avant and c.brain.registre.ouvertes()
    assert json.loads(c.memoire.meta("brain_missions"))[-1]["question"].startswith("pneus")


# ============================================================ 7. ancre du journal
def test_ancre_detecte_une_chaine_recalculee(tmp_path):
    gd = guardian(tmp_path, {GD.ARRET_URGENCE: "off"})
    for o in ("memoire_chercher", "outil_invente", "etat_core"):
        run(gd.decider(o, "adulte", mission="m"))
    ancre = gd.ancre()
    assert ancre["lignes"] == 3 and gd.verifier_ancre(ancre)
    # attaque : on change une décision ET on recalcule toute la chaîne
    db = gd.memoire.db
    db.execute("UPDATE guardian_journal SET autorise = 1, raison = 'ok' WHERE outil = 'outil_invente'")
    prec = "0" * 64
    for r in db.execute("SELECT id, quand, mission, profil, outil, classe, autorise, raison FROM guardian_journal"
                        " ORDER BY id").fetchall():
        h = GD.Guardian._empreinte(prec, {"quand": r[1], "mission": r[2], "profil": r[3], "outil": r[4],
                                          "classe": r[5], "autorise": r[6], "raison": r[7]})
        db.execute("UPDATE guardian_journal SET prec = ?, hash = ? WHERE id = ?", (prec, h, r[0]))
        prec = h
    assert gd.verifier_chaine()["ok"]  # la chaîne seule ne voit rien…
    assert not gd.verifier_ancre(ancre)  # …l'ancre publiée dans HA, si
    (tmp_path / "x").mkdir()
    assert "journal_ancre" in BR.Brain(FauxCore(tmp_path / "x"), gd).etat()["attributes"]
