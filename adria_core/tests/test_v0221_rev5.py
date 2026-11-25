"""v0.22.1 (révision 5) — 5e audit : la récupération exige un journal du Guardian EXISTANT, NON VIDE et INTÈGRE.

Condition historique démontrée (code de fb018ff) : depuis la v0.22.0, le Core crée `guardian_journal` à son
démarrage (main.Core.__init__, avant tout banc) et chaque mission du banc y écrit au moins une décision ; la
v0.21.3 n'a ni banc ni journal. Une base qui porte `banc_historique` a donc forcément un journal non vide.

Chaque refus est vérifié par une SECONDE connexion SQLite : compteur, marqueur et journal entier inchangés.
"""

import asyncio
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

sys.path.insert(0, os.path.dirname(__file__))
import banc as BANC  # noqa: E402
import aide_registre  # noqa: E402  (révision 6 : base simulée « ancrée depuis sa création »)


@pytest.fixture(autouse=True)
def _registre_simule(monkeypatch):
    monkeypatch.setattr(BANC, "recuperer", aide_registre.recuperer_avec_registre_simule)
import guardian as GD  # noqa: E402
from store import Memoire  # noqa: E402

J = datetime(2026, 11, 25, 18, 0)
DEMAIN = datetime(2026, 11, 26, 9, 0)
OK = BANC.CONFIRMATION
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "base_v0220.db")
HISTO = {"quand": "2026-11-25T17:42:29", "version": "0.22.0", "moteur": "chaîne", "score": 88, "reussis": 22,
         "total": 25, "par_categorie": {}, "latence_moyenne": 28.0}


def instantane(chemin):
    """Tout ce que la récupération pourrait toucher, relu par une connexion neuve."""
    with sqlite3.connect(chemin) as c:
        meta = dict(c.execute("SELECT cle, valeur FROM meta WHERE cle IN ('banc_initialise', 'banc_compte')"))
        table = c.execute("SELECT sql FROM sqlite_master WHERE name = 'guardian_journal'").fetchone()
        journal = [tuple(r) for r in c.execute("SELECT * FROM guardian_journal ORDER BY id")] if table else None
    return meta, table, journal


def chaine(chemin):
    with sqlite3.connect(chemin) as c:
        prec = "0" * 64
        for r in c.execute("SELECT quand, mission, profil, outil, classe, autorise, raison, prec, hash"
                           " FROM guardian_journal ORDER BY id"):
            contenu = dict(zip(("quand", "mission", "profil", "outil", "classe", "autorise", "raison"), r[:7]))
            if r[7] != prec or r[8] != GD.empreinte(prec, contenu):
                return False
            prec = r[8]
    return True


def base_v0220(tmp_path):
    chemin = str(tmp_path / "v0220.db")
    shutil.copyfile(FIXTURE, chemin)
    return chemin


def base_historique_seul(tmp_path, journal: str):
    """`banc_historique` présent, journal : « absent » | « vide » | « valide » | « rompu »."""
    chemin = str(tmp_path / f"h_{journal}.db")
    m = Memoire(chemin)
    BANC.garder(m, HISTO)
    if journal != "absent":
        gd = GD.Guardian(m, None)  # crée la table, comme le Core au démarrage
        if journal in ("valide", "rompu"):
            loop = asyncio.new_event_loop()
            loop.run_until_complete(gd.decider("memoire_chercher", "adulte", mission="m1"))
            loop.run_until_complete(gd.decider("etat_core", "adulte", mission="m1"))
        if journal == "rompu":
            m.db.execute("UPDATE guardian_journal SET raison = 'retouche' WHERE id = 1")
            m.db.commit()
    m.db.close()
    return chemin


def refuse_sans_rien_toucher(chemin, *motifs):
    avant = instantane(chemin)
    m = Memoire(chemin)
    ok, motif = BANC.recuperer(m, OK, J)
    m.db.close()
    assert not ok and all(x in motif for x in motifs), motif
    assert instantane(chemin) == avant  # compteur, marqueur, table et journal entier inchangés
    return motif


# ------------------------------------------------------------------ 6. base v0.22.0 représentative
def test_06_fixture_issue_du_vrai_code_v0220(tmp_path):
    chemin = base_v0220(tmp_path)
    meta, table, journal = instantane(chemin)
    with sqlite3.connect(chemin) as c:
        cles = {r[0] for r in c.execute("SELECT cle FROM meta")}
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert meta == {} and "banc_historique" in cles  # état exact de la production : ni compteur ni marqueur
    assert {"lignes", "meta", "ia_stats", "evolution", "procedures", "guardian_journal", "contradictions"} <= tables
    assert table and len(journal) >= 25 and chaine(chemin)  # 1 mission BRAIN + 25 scénarios, tous tracés
    assert not any(r[4] in ("banc_reservation", "banc_recuperation") for r in journal)


# ------------------------------------------------------------------ 3, 9, 12. seul scénario légitime
def test_03_09_12_recuperation_base_v0220_reelle(tmp_path):
    chemin = base_v0220(tmp_path)
    _, _, avant = instantane(chemin)
    m = Memoire(chemin)
    assert BANC.reserver_detail(m, J)[1].startswith("E :")
    assert BANC.recuperer(m, OK, J) == (True, "quota récupéré : prochain passage possible demain")
    m.db.close()
    meta, _, apres = instantane(chemin)
    assert apres[:len(avant)] == avant and len(apres) == len(avant) + 1  # ancien journal intact, 1 ligne ajoutée
    assert apres[-1][4] == "banc_recuperation" and "cas E" in apres[-1][7]
    assert apres[-1][8] == avant[-1][9]  # chaînée sur la dernière ligne existante (pas de nouvelle chaîne)
    assert chaine(chemin)
    assert json.loads(meta["banc_compte"]) == {"date": "2026-11-25", "n": BANC.MAX_JOUR, "v": 2}  # quota épuisé
    assert json.loads(meta["banc_initialise"])["origine"] == "recuperation"
    m = Memoire(chemin)
    assert BANC.reserver(m, J) is False and BANC.reserver_detail(m, DEMAIN) == (True, "passage 1/2")
    assert chaine(chemin)


def test_03_historique_avec_journal_valide(tmp_path):
    chemin = base_historique_seul(tmp_path, "valide")
    m = Memoire(chemin)
    assert BANC.recuperer(m, OK, J)[0]
    m.db.close()
    assert chaine(chemin) and json.loads(instantane(chemin)[0]["banc_compte"])["n"] == BANC.MAX_JOUR


# ------------------------------------------------------------------ 1, 2, 4, 8. journal absent, vide, rompu
def test_01_08_journal_absent_refus_et_jamais_cree(tmp_path):
    chemin = base_historique_seul(tmp_path, "absent")
    assert instantane(chemin)[1] is None
    refuse_sans_rien_toucher(chemin, "journal du Guardian absent", "pas recréé")
    assert instantane(chemin)[1] is None  # la table n'a PAS été créée


def test_02_journal_present_mais_vide(tmp_path):
    chemin = base_historique_seul(tmp_path, "vide")
    assert instantane(chemin)[2] == []
    refuse_sans_rien_toucher(chemin, "journal du Guardian vide")


def test_02b_journal_v0220_vide_apres_coup(tmp_path):
    """Base v0.22.0 réelle dont le journal a été vidé : anomalie, pas « journal sain »."""
    chemin = base_v0220(tmp_path)
    with sqlite3.connect(chemin) as c:
        c.execute("DELETE FROM guardian_journal")  # simulation d'une perte de l'historique Guardian
    refuse_sans_rien_toucher(chemin, "journal du Guardian vide")


def test_01b_journal_v0220_supprime(tmp_path):
    chemin = base_v0220(tmp_path)
    with sqlite3.connect(chemin) as c:
        c.execute("DROP TABLE guardian_journal")  # simulation d'une suppression de l'historique Guardian
    refuse_sans_rien_toucher(chemin, "journal du Guardian absent")
    assert instantane(chemin)[1] is None


@pytest.mark.parametrize("source", ["historique", "v0220"])
def test_04_journal_rompu(tmp_path, source):
    if source == "historique":
        chemin = base_historique_seul(tmp_path, "rompu")
    else:
        chemin = base_v0220(tmp_path)
        with sqlite3.connect(chemin) as c:
            c.execute("UPDATE guardian_journal SET autorise = 1 - autorise WHERE id = 5")
    assert not chaine(chemin)
    refuse_sans_rien_toucher(chemin, "journal du Guardian rompu", "ni réparée ni masquée")
    assert not chaine(chemin)  # toujours rompu : rien n'a été « réparé »


# ------------------------------------------------------------------ 5. lecture impossible
class Panne:
    def __init__(self, db, quand):
        self.db, self.quand = db, quand

    @property
    def in_transaction(self):
        return self.db.in_transaction

    def execute(self, sql, *a):
        if self.quand(sql):
            raise sqlite3.OperationalError("lecture impossible")
        return self.db.execute(sql, *a)

    def commit(self):
        return self.db.commit()

    def rollback(self):
        return self.db.rollback()


@pytest.mark.parametrize("cible", ["sqlite_master", "guardian_journal", "banc_historique"])
def test_05_lecture_sql_impossible(tmp_path, cible):
    chemin = base_v0220(tmp_path)
    avant = instantane(chemin)
    m = Memoire(chemin)
    vraie = m.db
    m.db = Panne(vraie, lambda sql: sql.startswith("SELECT") and cible in sql)
    ok, motif = BANC.recuperer(m, OK, J)
    assert not ok and "SQLite" in motif and not vraie.in_transaction
    m.db = vraie
    m.db.close()
    assert instantane(chemin) == avant


def test_05b_table_journal_illisible(tmp_path):
    """Table présente mais de structure inattendue (colonnes manquantes) : impossible de vérifier -> refus."""
    chemin = str(tmp_path / "x.db")
    m = Memoire(chemin)
    BANC.garder(m, HISTO)
    m.db.execute("CREATE TABLE guardian_journal (id INTEGER PRIMARY KEY, quand TEXT)")
    m.db.execute("INSERT INTO guardian_journal (quand) VALUES ('2026-11-25')")
    m.db.commit()
    m.db.close()
    refuse_sans_rien_toucher(chemin, "SQLite")


# ------------------------------------------------------------------ 7. refus sans aucune modification (tous cas)
def test_07_refus_autres_cas_sans_modification(tmp_path):
    vierge = str(tmp_path / "vierge.db")
    Memoire(vierge).db.close()
    refuse_sans_rien_toucher(vierge, "journal du Guardian absent")
    vierge_core = str(tmp_path / "vierge_core.db")  # base neuve d'un Core démarré : journal vide
    GD.Guardian(Memoire(vierge_core), None).memoire.db.close()
    refuse_sans_rien_toucher(vierge_core, "journal du Guardian vide")
    saine = base_v0220(tmp_path)
    m = Memoire(saine)
    assert BANC.recuperer(m, OK, J)[0]
    m.db.close()
    refuse_sans_rien_toucher(saine, "pas dans le cas E")


# ------------------------------------------------------------------ 10. double récupération
def test_10_double_recuperation(tmp_path):
    chemin = base_v0220(tmp_path)
    m = Memoire(chemin)
    assert BANC.recuperer(m, OK, J)[0]
    m.db.close()
    for _ in range(2):
        refuse_sans_rien_toucher(chemin, "pas dans le cas E")
    with sqlite3.connect(chemin) as c:
        assert c.execute("SELECT COUNT(*) FROM guardian_journal WHERE outil = 'banc_recuperation'").fetchone()[0] == 1


# ------------------------------------------------------------------ 11. atomicité
class PanneEcriture(Panne):
    def __init__(self, db, quand, au_commit=False):
        super().__init__(db, quand)
        self.au_commit = au_commit

    def commit(self):
        if self.au_commit:
            raise sqlite3.OperationalError("disque plein")
        return self.db.commit()


@pytest.mark.parametrize("panne", ["marqueur", "compteur", "journal", "commit"])
def test_11_atomicite(tmp_path, panne):
    chemin = base_v0220(tmp_path)
    avant = instantane(chemin)
    m = Memoire(chemin)
    vraie = m.db
    regles = {"marqueur": lambda sql: False, "compteur": lambda sql: False,
              "journal": lambda sql: sql.lstrip().startswith("INSERT INTO guardian_journal"), "commit": lambda sql: False}
    m.db = PanneEcriture(vraie, regles[panne], au_commit=(panne == "commit"))
    if panne in ("marqueur", "compteur"):
        cle = "banc_initialise" if panne == "marqueur" else "banc_compte"
        origine = m.db.execute

        def execute(sql, *a):
            if sql.startswith("INSERT INTO meta") and a and a[0][0] == cle:
                raise sqlite3.OperationalError("écriture impossible")
            return origine(sql, *a)
        m.db.execute = execute
    ok, motif = BANC.recuperer(m, OK, J)
    assert not ok and "SQLite" in motif and not vraie.in_transaction
    m.db = vraie
    m.db.close()
    assert instantane(chemin) == avant and chaine(chemin)
    m = Memoire(chemin)
    assert BANC.recuperer(m, OK, J)[0]  # une fois la panne passée, toujours récupérable une seule fois
    m.db.close()
    assert chaine(chemin)


def test_recuperer_ne_cree_jamais_la_table():
    import inspect
    vraie = aide_registre._original  # la VRAIE fonction, pas l'aide de test
    code = (inspect.getsource(vraie) + inspect.getsource(BANC._etat_journal)
            + inspect.getsource(BANC._controles_cas_E) + inspect.getsource(BANC.recuperer_non_verifiee))
    assert "TABLE_JOURNAL" not in code and "CREATE TABLE" not in code
