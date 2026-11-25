"""v0.22.1 (révision 2) — 2e audit : arrêt d'urgence sans fonction de lecture, quota du banc fermé par défaut."""

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


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


# ===================================================================== 1. arrêt d'urgence
def lecteur(valeur=None, erreur=None):
    async def etat(e):
        assert e == GD.ARRET_URGENCE
        if erreur:
            raise erreur
        return valeur
    return etat


CAS_BLOQUANTS = {
    "callback absent": None,
    "callback non appelable": "off",  # une chaîne n'est pas une fonction de lecture
    "retourne None": lecteur(None),
    "lève une exception": lecteur(erreur=OSError("HA injoignable")),
    "unknown": lecteur("unknown"),
    "unavailable": lecteur("unavailable"),
    "on": lecteur("on"),
    "OFF majuscules": lecteur("OFF"),
    "off avec espace": lecteur("off "),
    "type inattendu (bool)": lecteur(False),
    "type inattendu (dict)": lecteur({"state": "off"}),
}


@pytest.mark.parametrize("cas", sorted(CAS_BLOQUANTS))
def test_arret_urgence_ferme(tmp_path, cas):
    gd = GD.Guardian(Memoire(str(tmp_path / "g.db")), CAS_BLOQUANTS[cas])
    assert run(gd.arret_urgence()) is True
    # toutes les décisions protégées sont refusées, quelle que soit leur classe
    for outil in GD.REGISTRE:
        if outil == "etat_core":
            continue
        d = run(gd.decider(outil, "adulte", {"texte": "lampe", "entite": "light.salon"}))
        assert not d.autorise and "arrêt d'urgence" in d.raison, (cas, outil)
    # seule exception voulue : l'état technique du Core, sans donnée du foyer
    assert run(gd.decider("etat_core", "adulte")).autorise


def test_arret_urgence_off_exact_laisse_passer(tmp_path):
    gd = GD.Guardian(Memoire(str(tmp_path / "g.db")), lecteur("off"))
    assert run(gd.arret_urgence()) is False
    assert run(gd.decider("memoire_chercher", "adulte")).autorise
    # « off » ne lève pas les autres protections
    assert not run(gd.decider("etat_entite", "adulte", {"entite": "lock.porte"})).autorise
    assert not run(gd.decider("service_ha", "adulte", {"entity_id": "light.salon"})).autorise


def test_seule_exception_etat_core():
    import inspect
    code = inspect.getsource(GD.Guardian._decider)
    assert code.count('"etat_core"') == 1  # aucune autre exception à l'arrêt d'urgence


# ===================================================================== 2. quota du banc
def mem(tmp_path, nom="b.db"):
    return Memoire(str(tmp_path / nom))


def poser(m, valeur: str):
    m.db.execute("INSERT OR REPLACE INTO meta (cle, valeur) VALUES ('banc_compte', ?)", (valeur,))
    m.db.commit()


def compteur(m):
    r = m.db.execute("SELECT valeur FROM meta WHERE cle = 'banc_compte'").fetchone()
    return r[0] if r else None


@pytest.mark.parametrize("valeur", [
    "{pas du json", "", "null", "[]", "42", '"texte"',
    '{"date": "2026-11-25", "n": -1}',          # négatif
    '{"date": "2026-11-24", "n": -5}',          # négatif, même avec une date passée
    '{"date": "2026-11-25", "n": "0"}',         # type incorrect
    '{"date": "2026-11-25", "n": 1.0}',
    '{"date": "2026-11-25", "n": true}',
    '{"date": "2026-11-25", "n": null}',
    '{"date": 20261125, "n": 0}',
    '{"date": "25/11/2026", "n": 0}',
    '{"date": "2026-11-25"}',                   # champ manquant
    '{"date": "2026-11-25", "n": 0, "x": 1}',   # champ inattendu
    '{"date": "2026-11-26", "n": 0}',           # date dans le futur
])
def test_compteur_corrompu_refuse(tmp_path, valeur):
    m = mem(tmp_path)
    poser(m, valeur)
    assert BANC.reserver(m, J) is False
    assert compteur(m) == valeur  # rien n'est réécrit : pas de remise à zéro silencieuse
    assert not m.db.in_transaction


def test_quota_atteint_et_changement_de_date(tmp_path):
    m = mem(tmp_path)
    assert compteur(m) is None
    assert BANC.reserver(m, J) and BANC.reserver(m, J)
    assert BANC.reserver(m, J) is False
    assert json.loads(compteur(m)) == {"date": "2026-11-25", "n": 2}
    poser(m, '{"date": "2026-11-25", "n": 7}')  # au-delà du plafond (valide) : refus
    assert BANC.reserver(m, J) is False
    lendemain = datetime(2026, 11, 26, 0, 1)
    assert BANC.reserver(m, lendemain)  # date antérieure valide : remise à zéro autorisée
    assert json.loads(compteur(m)) == {"date": "2026-11-26", "n": 1}


def test_quota_survit_au_redemarrage(tmp_path):
    m = mem(tmp_path)
    assert BANC.reserver(m, J) and BANC.reserver(m, J)
    m.db.close()
    m2 = mem(tmp_path)  # nouvelle connexion = redémarrage du Core
    assert BANC.reserver(m2, J) is False


class Base:
    """Enveloppe d'une connexion sqlite qui fait échouer une étape choisie."""

    def __init__(self, db, echec: str):
        self.db, self.echec = db, echec

    @property
    def in_transaction(self):
        return self.db.in_transaction

    def execute(self, sql, *a):
        s = sql.strip().upper()
        if (self.echec == "lecture" and s.startswith("SELECT")) or \
           (self.echec == "ecriture" and s.startswith("INSERT")) or \
           (self.echec == "verrou" and s.startswith("BEGIN")):
            raise sqlite3.OperationalError(f"{self.echec} impossible")
        return self.db.execute(sql, *a)

    def commit(self):
        if self.echec == "commit":
            raise sqlite3.OperationalError("disque plein")
        return self.db.commit()

    def rollback(self):
        return self.db.rollback()


@pytest.mark.parametrize("echec", ["lecture", "ecriture", "verrou", "commit"])
def test_erreur_sqlite_refuse(tmp_path, echec):
    m = mem(tmp_path)
    vraie = m.db
    m.db = Base(vraie, echec)
    assert BANC.reserver(m, J) is False
    assert not vraie.in_transaction  # transaction annulée, base non verrouillée
    m.db = vraie
    assert compteur(m) is None  # aucune écriture partielle
    assert BANC.reserver(m, J)  # la base reste utilisable une fois l'erreur passée


def test_transaction_deja_ouverte_refuse(tmp_path):
    m = mem(tmp_path)
    m.db.execute("INSERT INTO meta (cle, valeur) VALUES ('x', 'y')")  # transaction implicite ouverte
    assert m.db.in_transaction
    assert BANC.reserver(m, J) is False
    m.db.rollback()


def test_reservations_concurrentes(tmp_path):
    chemin = str(tmp_path / "c.db")
    Memoire(chemin).db.close()
    depart = threading.Barrier(12)
    resultats = []

    def essayer():
        m = Memoire(chemin)  # une connexion par fil, comme deux processus distincts
        depart.wait()
        resultats.append(BANC.reserver(m, J))
        m.db.close()
    fils = [threading.Thread(target=essayer) for _ in range(12)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    assert sum(resultats) == BANC.MAX_JOUR == 2
    assert json.loads(compteur(Memoire(chemin))) == {"date": "2026-11-25", "n": 2}
