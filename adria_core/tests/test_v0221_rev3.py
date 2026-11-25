"""v0.22.1 (révision 3) — 3e audit : l'absence du compteur du banc n'est plus une « première utilisation ».

Chaque test relit les données PERSISTÉES par une connexion SQLite neuve (pas seulement le retour).
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime

import pytest

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

import banc as BANC  # noqa: E402
import guardian as GD  # noqa: E402
from store import Memoire  # noqa: E402

J = datetime(2026, 11, 25, 18, 0)
DEMAIN = datetime(2026, 11, 26, 0, 1)


# ------------------------------------------------------------------ lecture indépendante de la base
def lu(chemin, cle):
    with sqlite3.connect(chemin) as c:
        r = c.execute("SELECT valeur FROM meta WHERE cle = ?", (cle,)).fetchone()
    return None if r is None else json.loads(r[0])


def lignes_banc(chemin):
    with sqlite3.connect(chemin) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name = 'guardian_journal'").fetchone():
            return []
        return [r[0] for r in c.execute("SELECT outil FROM guardian_journal WHERE classe = 'banc' ORDER BY id")]


def ecrire(chemin, cle, valeur):
    with sqlite3.connect(chemin) as c:
        if valeur is None:
            c.execute("DELETE FROM meta WHERE cle = ?", (cle,))  # simulation d'une suppression accidentelle
        else:
            c.execute("INSERT OR REPLACE INTO meta (cle, valeur) VALUES (?, ?)", (cle, valeur))


def chaine_ok(chemin):
    m = Memoire(chemin)
    return GD.Guardian(m, None).verifier_chaine()["ok"]


@pytest.fixture
def base(tmp_path):
    chemin = str(tmp_path / "banc.db")
    return chemin, Memoire(chemin)


# ------------------------------------------------------------------ 1-2. base vierge
def test_01_base_vierge_premiere_reservation(base):
    chemin, m = base
    assert BANC.reserver_detail(m, J) == (True, "passage 1/2 (neuve)")
    assert lu(chemin, "banc_compte") == {"date": "2026-11-25", "n": 1, "v": 2}


def test_02_compteur_marqueur_et_journal_crees_ensemble(base):
    chemin, m = base
    assert lu(chemin, "banc_compte") is None and lu(chemin, "banc_initialise") is None
    BANC.reserver(m, J)
    assert lu(chemin, "banc_initialise") == {"v": 1, "cree": "2026-11-25T18:00:00", "origine": "neuve"}
    assert lu(chemin, "banc_compte")["n"] == 1
    assert lignes_banc(chemin) == ["banc_reservation"] and chaine_ok(chemin)


# ------------------------------------------------------------------ 3. migration rétrocompatible
def test_03_migration_ancien_compteur_sans_remise_a_zero(base):
    chemin, m = base
    ecrire(chemin, "banc_compte", '{"date": "2026-11-25", "n": 1}')  # format des premières v0.22.1
    assert BANC.reserver_detail(m, J) == (True, "passage 2/2 (migration)")
    assert lu(chemin, "banc_compte") == {"date": "2026-11-25", "n": 2, "v": 2}  # consommation conservée
    assert lu(chemin, "banc_initialise")["origine"] == "migration"
    assert BANC.reserver(m, J) is False


def test_03b_migration_quota_deja_atteint_ne_touche_rien(base):
    chemin, m = base
    ecrire(chemin, "banc_compte", '{"date": "2026-11-25", "n": 2}')
    ok, motif = BANC.reserver_detail(m, J)
    assert not ok and "quota atteint" in motif
    assert lu(chemin, "banc_compte") == {"date": "2026-11-25", "n": 2} and lu(chemin, "banc_initialise") is None


def test_03c_migration_compteur_de_la_veille(base):
    chemin, m = base
    ecrire(chemin, "banc_compte", '{"date": "2026-11-24", "n": 2}')
    assert BANC.reserver(m, J)
    assert lu(chemin, "banc_compte") == {"date": "2026-11-25", "n": 1, "v": 2}


# ------------------------------------------------------------------ 4-7. suppressions
def test_04_marqueur_sans_compteur_refus(base):
    chemin, m = base
    ecrire(chemin, "banc_initialise", json.dumps({"v": 1, "cree": "2026-11-20T10:00:00", "origine": "neuve"}))
    ok, motif = BANC.reserver_detail(m, J)
    assert not ok and motif.startswith("C :")
    assert lu(chemin, "banc_compte") is None  # pas de recréation


@pytest.mark.parametrize("reservations", [1, 2])
def test_05_06_compteur_supprime_apres_usage(base, reservations):
    chemin, m = base
    for _ in range(reservations):
        assert BANC.reserver(m, J)
    ecrire(chemin, "banc_compte", None)
    for jour in (J, DEMAIN):  # même le lendemain : pas de remise à zéro
        ok, motif = BANC.reserver_detail(m, jour)
        assert not ok and motif.startswith("C :")
    assert lu(chemin, "banc_compte") is None and lignes_banc(chemin) == ["banc_reservation"] * reservations


def test_07_marqueur_supprime_apres_usage(base):
    chemin, m = base
    assert BANC.reserver(m, J)
    ecrire(chemin, "banc_initialise", None)
    for jour in (J, DEMAIN):
        ok, motif = BANC.reserver_detail(m, jour)
        assert not ok and "marqueur absent" in motif
    assert lu(chemin, "banc_initialise") is None  # pas recréé automatiquement
    assert lu(chemin, "banc_compte") == {"date": "2026-11-25", "n": 1, "v": 2}


def test_07b_compteur_et_marqueur_supprimes_apres_usage(base):
    chemin, m = base
    assert BANC.reserver(m, J)
    ecrire(chemin, "banc_initialise", None)
    ecrire(chemin, "banc_compte", None)
    ok, motif = BANC.reserver_detail(m, DEMAIN)
    assert not ok and motif.startswith("E :") and "journal du Guardian" in motif
    assert lu(chemin, "banc_compte") is None and lu(chemin, "banc_initialise") is None


# ------------------------------------------------------------------ 8-9. corruption
@pytest.mark.parametrize("marqueur", [
    "{cassé", "null", "[]", '{"v": 1}', '{"v": 2, "cree": "2026-11-25T18:00:00", "origine": "neuve"}',
    '{"v": true, "cree": "2026-11-25T18:00:00", "origine": "neuve"}',
    '{"v": 1, "cree": "hier", "origine": "neuve"}', '{"v": 1, "cree": "2026-11-25T18:00:00", "origine": "pirate"}',
    '{"v": 1, "cree": "2026-11-25T18:00:00", "origine": "neuve", "x": 0}'])
def test_08_marqueur_corrompu(base, marqueur):
    chemin, m = base
    assert BANC.reserver(m, J)
    avant = lu(chemin, "banc_compte")
    ecrire(chemin, "banc_initialise", marqueur)
    ok, motif = BANC.reserver_detail(m, J)
    assert not ok and motif.startswith("D :"), motif
    assert lu(chemin, "banc_compte") == avant  # rien n'est réparé ni réécrit


@pytest.mark.parametrize("compteur", [  # tous doivent donner un refus de type D (ou date future)
    "{cassé", '{"date": "2026-11-25", "n": -1, "v": 2}', '{"date": "2026-11-25", "n": "0", "v": 2}',
    '{"date": "2026-11-25", "n": 0, "v": 3}', '{"date": "2026-11-25", "n": 0}',  # ancien format AVEC marqueur
    '{"date": "2026-11-27", "n": 0, "v": 2}'])
def test_09_compteur_corrompu(base, compteur):
    chemin, m = base
    assert BANC.reserver(m, J)
    ecrire(chemin, "banc_compte", compteur)
    assert BANC.reserver(m, J) is False
    with sqlite3.connect(chemin) as c:
        assert c.execute("SELECT valeur FROM meta WHERE cle = 'banc_compte'").fetchone()[0] == compteur


# ------------------------------------------------------------------ 10-12. pannes et interruptions
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


@pytest.mark.parametrize("cible", ["marqueur", "journal", "commit"])
def test_10_11_echec_ecriture_ou_commit(base, cible):
    chemin, m = base
    vraie = m.db
    regles = {
        "marqueur": lambda sql, a: sql.startswith("INSERT INTO meta") and a and a[0][0] == "banc_initialise",
        "journal": lambda sql, a: sql.lstrip().startswith("INSERT INTO guardian_journal"),
        "commit": lambda sql, a: sql == "COMMIT",
    }
    m.db = Panne(vraie, regles[cible])
    assert BANC.reserver(m, J) is False
    assert not vraie.in_transaction
    assert lu(chemin, "banc_compte") is None and lu(chemin, "banc_initialise") is None
    assert lignes_banc(chemin) == []
    m.db = vraie  # la panne passée : la base est toujours vierge, une seule réservation est accordée
    assert BANC.reserver_detail(m, J) == (True, "passage 1/2 (neuve)")


INTERRUPTION = r'''
import sys
sys.path.insert(0, sys.argv[1])
import os, banc as BANC
from datetime import datetime
from store import Memoire
m = Memoire(sys.argv[2])
m.db.execute("BEGIN IMMEDIATE")
marqueur = {"v": 1, "cree": "2026-11-25T18:00:00", "origine": "neuve"} if sys.argv[3] == "1" else None
BANC._ecrire(m.db, datetime(2026, 11, 25).date(), 0, marqueur,  # compteur remis à 0 en cours d'écriture
             datetime(2026, 11, 25, 18), "banc_reservation", "interrompu")
os._exit(9)  # arrêt brutal du processus AVANT le commit
'''


def test_12_interruption_pendant_ecriture(tmp_path):
    chemin = str(tmp_path / "i.db")
    Memoire(chemin).db.close()
    r = subprocess.run([sys.executable, "-I", "-c", INTERRUPTION, APP, chemin, "1"], capture_output=True)
    assert r.returncode == 9, r.stderr
    assert lu(chemin, "banc_compte") is None and lu(chemin, "banc_initialise") is None and lignes_banc(chemin) == []
    m = Memoire(chemin)
    assert BANC.reserver(m, J) and BANC.reserver(m, J) and not BANC.reserver(m, J)  # toujours 2, pas plus
    # interruption d'une réservation suivante : l'état déjà consommé reste intact
    m.db.close()
    r = subprocess.run([sys.executable, "-I", "-c", INTERRUPTION, APP, chemin, "0"], capture_output=True)
    assert r.returncode == 9, r.stderr
    assert lu(chemin, "banc_compte") == {"date": "2026-11-25", "n": 2, "v": 2}
    assert BANC.reserver(Memoire(chemin), J) is False


# ------------------------------------------------------------------ 13-14. redémarrage et date
def test_13_redemarrage(base):
    chemin, m = base
    assert BANC.reserver(m, J)
    m.db.close()
    m2 = Memoire(chemin)
    assert lu(chemin, "banc_initialise")["origine"] == "neuve"
    assert BANC.reserver_detail(m2, J) == (True, "passage 2/2")
    m2.db.close()
    assert BANC.reserver(Memoire(chemin), J) is False


def test_14_changement_de_date(base):
    chemin, m = base
    assert BANC.reserver(m, J) and BANC.reserver(m, J)
    assert BANC.reserver(m, DEMAIN)
    assert lu(chemin, "banc_compte") == {"date": "2026-11-26", "n": 1, "v": 2}
    assert lu(chemin, "banc_initialise")["origine"] == "neuve"  # le marqueur n'est jamais réécrit
    ok, motif = BANC.reserver_detail(m, J)  # retour dans le passé : la date du compteur est « future »
    assert not ok and "futur" in motif


# ------------------------------------------------------------------ 15-16. concurrence
def _course(chemin, n_fils, cible):
    depart = threading.Barrier(n_fils)
    res = []

    def fil(i):
        mem = Memoire(chemin)
        depart.wait()
        res.append(cible(mem, i))
        mem.db.close()
    fils = [threading.Thread(target=fil, args=(i,)) for i in range(n_fils)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    return res


def test_15_reservations_concurrentes(tmp_path):
    chemin = str(tmp_path / "c.db")
    Memoire(chemin).db.close()
    res = _course(chemin, 12, lambda mem, i: BANC.reserver(mem, J))
    assert sum(res) == 2
    assert lu(chemin, "banc_compte") == {"date": "2026-11-25", "n": 2, "v": 2}
    assert lignes_banc(chemin) == ["banc_reservation"] * 2 and chaine_ok(chemin)


def test_16_suppression_ou_corruption_concurrente(tmp_path):
    chemin = str(tmp_path / "c.db")
    m = Memoire(chemin)
    assert BANC.reserver(m, J)  # base initialisée, 1 passage consommé
    m.db.close()

    def acteur(mem, i):
        if i % 3 == 0:  # saboteurs : suppression ou corruption du compteur / du marqueur
            cle = "banc_compte" if i % 2 == 0 else "banc_initialise"
            try:
                with mem.db:
                    if i % 4 == 0:
                        mem.db.execute("DELETE FROM meta WHERE cle = ?", (cle,))
                    else:
                        mem.db.execute("UPDATE meta SET valeur = '{cassé' WHERE cle = ?", (cle,))
            except sqlite3.Error:
                pass
            return False
        return BANC.reserver(mem, J) or BANC.reserver(mem, DEMAIN)

    res = _course(chemin, 12, acteur)
    # comptage par jour sur les données PERSISTÉES (journal chaîné) : chaque passage est « passage k/2 du JOUR »
    with sqlite3.connect(chemin) as c:
        raisons = [r[0] for r in c.execute(
            "SELECT raison FROM guardian_journal WHERE outil = 'banc_reservation' ORDER BY id")]
    par_jour: dict[str, list[int]] = {}
    for r in raisons:
        k, jour = int(r.split()[1].split("/")[0]), r.split(" du ")[1][:10]
        par_jour.setdefault(jour, []).append(k)
    assert len(raisons) == 1 + sum(res) and chaine_ok(chemin)
    for jour, ks in par_jour.items():
        assert len(ks) <= 2 and ks == list(range(1, len(ks) + 1)), (jour, ks)  # jamais de remise à zéro
    assert len(par_jour.get("2026-11-25", [])) <= 2 and len(par_jour.get("2026-11-26", [])) <= 2


# ------------------------------------------------------------------ cas E et récupération manuelle
def test_cas_E_base_v0220_deja_utilisee(base):
    chemin, m = base
    BANC.garder(m, {"quand": "2026-11-25T17:42:29", "version": "0.22.0", "moteur": "chaîne", "score": 88,
                    "reussis": 22, "total": 25, "par_categorie": {}, "latence_moyenne": 28.0})
    ok, motif = BANC.reserver_detail(m, J)
    assert not ok and motif.startswith("E :") and "historique du banc" in motif
    assert lu(chemin, "banc_compte") is None and lu(chemin, "banc_initialise") is None


def test_cas_E_journal_rompu(base):
    chemin, m = base
    gd = GD.Guardian(m, None)
    run_decider = __import__("asyncio").new_event_loop().run_until_complete
    run_decider(gd.decider("memoire_chercher", "adulte"))
    run_decider(gd.decider("etat_core", "adulte"))
    m.db.execute("UPDATE guardian_journal SET raison = 'retouche' WHERE id = 1")
    m.db.commit()
    ok, motif = BANC.reserver_detail(m, J)
    assert not ok and "journal du Guardian rompu" in motif


def test_journal_brain_seul_ne_bloque_pas(base):
    """Des décisions BRAIN (journal intègre) ne sont pas une trace d'usage du BANC."""
    chemin, m = base
    gd = GD.Guardian(m, lambda e: None)
    __import__("asyncio").new_event_loop().run_until_complete(gd.decider("memoire_chercher", "adulte"))
    assert BANC.reserver_detail(m, J) == (True, "passage 1/2 (neuve)")
    assert chaine_ok(chemin)


def test_recuperation_manuelle(base):
    chemin, m = base
    BANC.garder(m, {"quand": "x", "version": "0.22.0", "moteur": "chaîne", "score": 88, "reussis": 22,
                    "total": 25, "par_categorie": {}, "latence_moyenne": 28.0})
    assert BANC.recuperer(m, "oui", J) == (False, "confirmation absente ou incorrecte")
    assert lu(chemin, "banc_compte") is None
    assert BANC.recuperer(m, BANC.CONFIRMATION, J)[0]
    assert lu(chemin, "banc_compte") == {"date": "2026-11-25", "n": 2, "v": 2}  # quota du jour épuisé
    assert lu(chemin, "banc_initialise")["origine"] == "recuperation"
    assert BANC.reserver(m, J) is False  # jamais de passage le jour même
    assert BANC.reserver_detail(m, DEMAIN) == (True, "passage 1/2")
    assert lignes_banc(chemin) == ["banc_recuperation", "banc_reservation"] and chaine_ok(chemin)


def test_le_core_n_appelle_jamais_recuperer():
    for nom in os.listdir(APP):
        if nom.endswith(".py") and nom != "banc.py":
            with open(os.path.join(APP, nom), encoding="utf-8") as f:
                assert "recuperer(" not in f.read() or nom == "web.py" and "BANC" not in f.read(), nom
