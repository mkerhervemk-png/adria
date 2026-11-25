"""v0.22.1 (révision 4) — 4e audit : banc.recuperer() n'agit QUE sur un cas E prouvé, sans rien écraser.

Toutes les vérifications relisent la base par une connexion SQLite neuve.
"""

import asyncio
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import banc as BANC  # noqa: E402
import guardian as GD  # noqa: E402
from store import Memoire  # noqa: E402

J = datetime(2026, 11, 25, 18, 0)
DEMAIN = datetime(2026, 11, 26, 9, 0)
OK = BANC.CONFIRMATION
HISTO = {"quand": "2026-11-25T17:42:29", "version": "0.22.0", "moteur": "chaîne", "score": 88, "reussis": 22,
         "total": 25, "par_categorie": {}, "latence_moyenne": 28.0}


def etat_persiste(chemin):
    """(marqueur, compteur, lignes banc du journal, chaîne intègre) relus par une connexion neuve."""
    with sqlite3.connect(chemin) as c:
        meta = dict(c.execute("SELECT cle, valeur FROM meta WHERE cle IN ('banc_initialise', 'banc_compte')"))
        lignes = []
        if c.execute("SELECT 1 FROM sqlite_master WHERE name = 'guardian_journal'").fetchone():
            lignes = [r[0] for r in c.execute("SELECT outil FROM guardian_journal WHERE classe = 'banc' ORDER BY id")]
    m = Memoire(chemin)
    ok = GD.Guardian(m, None).verifier_chaine()["ok"]
    m.db.close()
    return meta.get("banc_initialise"), meta.get("banc_compte"), lignes, ok


def ecrire(chemin, cle, valeur):
    with sqlite3.connect(chemin) as c:
        if valeur is None:
            c.execute("DELETE FROM meta WHERE cle = ?", (cle,))
        else:
            c.execute("INSERT OR REPLACE INTO meta (cle, valeur) VALUES (?, ?)", (cle, valeur))


@pytest.fixture
def base(tmp_path):
    chemin = str(tmp_path / "r.db")
    return chemin, Memoire(chemin)


def cas_E_historique(chemin, m):
    BANC.garder(m, HISTO)  # base v0.22.0 déjà passée au banc


def cas_E_journal(chemin, m):
    assert BANC.reserver(m, J)  # trace « banc_reservation » au journal…
    ecrire(chemin, "banc_initialise", None)  # …puis perte du compteur ET du marqueur
    ecrire(chemin, "banc_compte", None)


# ===================================================================== acceptation : cas E prouvé
@pytest.mark.parametrize("preparer", [cas_E_historique, cas_E_journal], ids=["historique_v0220", "journal"])
def test_acceptation_cas_E(base, preparer):
    chemin, m = base
    preparer(chemin, m)
    assert BANC.reserver_detail(m, J)[1].startswith("E :")
    _, _, avant, _ = etat_persiste(chemin)
    assert BANC.recuperer(m, OK, J) == (True, "quota récupéré : prochain passage possible demain")
    marqueur, compteur, lignes, chaine = etat_persiste(chemin)
    assert json.loads(marqueur) == {"v": 1, "cree": "2026-11-25T18:00:00", "origine": "recuperation"}
    assert json.loads(compteur) == {"date": "2026-11-25", "n": BANC.MAX_JOUR, "v": 2}
    assert lignes == avant + ["banc_recuperation"] and chaine
    # 8. quota épuisé le jour même, retour le lendemain
    assert BANC.reserver_detail(m, J) == (False, f"quota atteint ({BANC.MAX_JOUR}/{BANC.MAX_JOUR} aujourd'hui)")
    assert BANC.reserver_detail(m, DEMAIN) == (True, "passage 1/2")
    assert json.loads(etat_persiste(chemin)[1]) == {"date": "2026-11-26", "n": 1, "v": 2}


def test_trace_de_la_recuperation_dans_le_journal(base):
    chemin, m = base
    cas_E_historique(chemin, m)
    BANC.recuperer(m, OK, J)
    with sqlite3.connect(chemin) as c:
        raison = c.execute("SELECT raison FROM guardian_journal WHERE outil = 'banc_recuperation'").fetchone()[0]
    assert "cas E" in raison and "historique du banc" in raison


# ===================================================================== refus
def test_refus_confirmation(base):
    chemin, m = base
    cas_E_historique(chemin, m)
    for phrase in ("", "oui", OK.lower(), OK + " ", None):
        assert BANC.recuperer(m, phrase, J) == (False, "confirmation absente ou incorrecte")
    assert etat_persiste(chemin)[:3] == (None, None, [])


def test_refus_base_vierge(base):
    chemin, m = base
    ok, motif = BANC.recuperer(m, OK, J)
    assert not ok and "aucune trace d'usage" in motif
    assert etat_persiste(chemin)[:3] == (None, None, [])
    assert BANC.reserver_detail(m, J) == (True, "passage 1/2 (neuve)")  # la voie normale reste ouverte


def test_refus_base_vierge_avec_decisions_brain(base):
    chemin, m = base
    asyncio.new_event_loop().run_until_complete(GD.Guardian(m, None).decider("etat_core", "adulte"))
    ok, motif = BANC.recuperer(m, OK, J)
    assert not ok and "aucune trace d'usage" in motif


def test_refus_base_saine_initialisee(base):
    chemin, m = base
    assert BANC.reserver(m, J)
    avant = etat_persiste(chemin)
    ok, motif = BANC.recuperer(m, OK, J)
    assert not ok and "marqueur et compteur présent" in motif
    assert etat_persiste(chemin) == avant  # rien d'écrasé


def test_refus_cas_B_ancien_compteur(base):
    chemin, m = base
    cas_E_historique(chemin, m)
    ecrire(chemin, "banc_compte", '{"date": "2026-11-25", "n": 1}')
    ok, motif = BANC.recuperer(m, OK, J)
    assert not ok and "compteur présent" in motif
    assert etat_persiste(chemin)[:3] == (None, '{"date": "2026-11-25", "n": 1}', [])


def test_refus_cas_C_marqueur_sans_compteur(base):
    chemin, m = base
    assert BANC.reserver(m, J)
    ecrire(chemin, "banc_compte", None)
    marqueur = etat_persiste(chemin)[0]
    ok, motif = BANC.recuperer(m, OK, J)
    assert not ok and "marqueur présent" in motif
    assert etat_persiste(chemin)[:3] == (marqueur, None, ["banc_reservation"])


@pytest.mark.parametrize("cle,valeur", [
    ("banc_initialise", "{cassé"), ("banc_compte", "{cassé"),
    ("banc_compte", '{"date": "2026-11-25", "n": -1, "v": 2}'),
])
def test_refus_cas_D_corrompu(base, cle, valeur):
    chemin, m = base
    cas_E_historique(chemin, m)
    ecrire(chemin, cle, valeur)
    avant = etat_persiste(chemin)
    ok, motif = BANC.recuperer(m, OK, J)
    assert not ok and "pas dans le cas E" in motif
    assert etat_persiste(chemin) == avant  # la donnée corrompue est laissée telle quelle, pas écrasée


def test_refus_cas_D_compteur_v2_sans_marqueur(base):
    chemin, m = base
    assert BANC.reserver(m, J)
    ecrire(chemin, "banc_initialise", None)
    avant = etat_persiste(chemin)
    ok, motif = BANC.recuperer(m, OK, J)
    assert not ok and "compteur présent" in motif and etat_persiste(chemin) == avant


def test_refus_journal_rompu(base):
    chemin, m = base
    cas_E_historique(chemin, m)
    loop = asyncio.new_event_loop()
    gd = GD.Guardian(m, None)
    loop.run_until_complete(gd.decider("etat_core", "adulte"))
    loop.run_until_complete(gd.decider("memoire_chercher", "adulte"))
    m.db.execute("UPDATE guardian_journal SET raison = 'retouche' WHERE id = 1")
    m.db.commit()
    ok, motif = BANC.recuperer(m, OK, J)
    assert not ok and "journal du Guardian rompu" in motif and "ni réparée ni masquée" in motif
    marqueur, compteur, lignes, chaine = etat_persiste(chemin)
    assert (marqueur, compteur, lignes, chaine) == (None, None, [], False)  # rien ajouté, toujours rompu


def test_refus_journal_rompu_seule_trace(base):
    """Journal rompu sans autre trace : cas E pour reserver (refus), et pas de récupération non plus."""
    chemin, m = base
    loop = asyncio.new_event_loop()
    gd = GD.Guardian(m, None)
    loop.run_until_complete(gd.decider("etat_core", "adulte"))
    m.db.execute("UPDATE guardian_journal SET hash = 'x'")
    m.db.commit()
    assert BANC.reserver_detail(m, J)[1].startswith("E :")
    assert not BANC.recuperer(m, OK, J)[0]


# ===================================================================== double récupération
def test_double_recuperation(base):
    chemin, m = base
    cas_E_historique(chemin, m)
    assert BANC.recuperer(m, OK, J)[0]
    apres = etat_persiste(chemin)
    for jour in (J, DEMAIN):
        ok, motif = BANC.recuperer(m, OK, jour)
        assert not ok and "pas dans le cas E" in motif
    assert etat_persiste(chemin) == apres  # la 2e récupération n'a rien écrit


def test_recuperations_concurrentes(tmp_path):
    chemin = str(tmp_path / "c.db")
    m = Memoire(chemin)
    cas_E_historique(chemin, m)
    m.db.close()
    depart, res = threading.Barrier(10), []

    def fil():
        mem = Memoire(chemin)
        depart.wait()
        res.append(BANC.recuperer(mem, OK, J)[0])
        mem.db.close()
    fils = [threading.Thread(target=fil) for _ in range(10)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    assert sum(res) == 1
    assert etat_persiste(chemin)[2] == ["banc_recuperation"] and etat_persiste(chemin)[3]


# ===================================================================== échecs de transaction
class Panne:
    def __init__(self, db, quand):
        self.db, self.quand = db, quand

    @property
    def in_transaction(self):
        return self.db.in_transaction

    def execute(self, sql, *a):
        if self.quand(sql, a):
            raise sqlite3.OperationalError("panne simulée")
        return self.db.execute(sql, *a)

    def commit(self):
        if self.quand("COMMIT", ()):
            raise sqlite3.OperationalError("panne simulée au commit")
        return self.db.commit()

    def rollback(self):
        return self.db.rollback()


PANNES = {
    "verrou": lambda sql, a: sql.startswith("BEGIN"),
    "lecture": lambda sql, a: sql.startswith("SELECT 1 FROM meta"),
    "marqueur": lambda sql, a: sql.startswith("INSERT INTO meta") and a and a[0][0] == "banc_initialise",
    "compteur": lambda sql, a: sql.startswith("INSERT INTO meta") and a and a[0][0] == "banc_compte",
    "journal": lambda sql, a: sql.lstrip().startswith("INSERT INTO guardian_journal"),
    "commit": lambda sql, a: sql == "COMMIT",
}


@pytest.mark.parametrize("panne", sorted(PANNES))
def test_echec_de_transaction(base, panne):
    chemin, m = base
    cas_E_historique(chemin, m)
    vraie = m.db
    m.db = Panne(vraie, PANNES[panne])
    ok, motif = BANC.recuperer(m, OK, J)
    assert not ok and ("SQLite" in motif or "verrou" in motif), motif
    assert not vraie.in_transaction
    assert etat_persiste(chemin)[:3] == (None, None, [])  # atomicité : rien de partiel
    m.db = vraie
    assert BANC.recuperer(m, OK, J)[0]  # la panne passée, le cas E est toujours récupérable une fois


def test_transaction_deja_ouverte(base):
    chemin, m = base
    cas_E_historique(chemin, m)
    m.db.execute("INSERT INTO meta (cle, valeur) VALUES ('x', 'y')")
    assert BANC.recuperer(m, OK, J) == (False, "transaction déjà ouverte")
    m.db.rollback()
    assert etat_persiste(chemin)[:3] == (None, None, [])


def test_le_core_n_appelle_jamais_recuperer():
    app = os.path.join(os.path.dirname(__file__), "..", "app")
    for nom in os.listdir(app):
        if nom.endswith(".py") and nom != "banc.py":
            with open(os.path.join(app, nom), encoding="utf-8") as f:
                assert "recuperer(" not in f.read(), nom
