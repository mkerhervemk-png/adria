"""AIDE DE TEST UNIQUEMENT (révision 6) — simule une base ANCRÉE DEPUIS SA CRÉATION.

Les tests des révisions 3 à 5 vérifient les contrôles de `recuperer()` qui précèdent celui du registre
(cas E, journal absent/vide/rompu, transactions…). Depuis la révision 6, `recuperer()` exige en plus un
registre qui prouve la complétude. Pour garder ces tests utiles, `recuperer_avec_registre_simule()` fournit
un registre qui ancre chaque ligne DÉJÀ présente dans le journal (comme l'aurait fait un Core 0.22.1 depuis le
premier jour). Ce n'est jamais utilisé par le code de production : le Core ne reconstruit jamais un registre.
"""
import os
import sqlite3

import ancrage as AN
import banc as BANC

_original = BANC.recuperer


def _chemin(memoire) -> str:
    vraie = getattr(memoire.db, "db", memoire.db)
    return vraie.execute("PRAGMA database_list").fetchone()[2]


def registre_simule(chemin_db: str) -> AN.Registre:
    reg = AN.Registre(chemin_db + ".ancres")
    with reg.verrou():
        if not os.path.exists(reg.chemin):
            with sqlite3.connect(chemin_db) as c:
                if c.execute("SELECT 1 FROM sqlite_master WHERE name = 'guardian_journal'").fetchone():
                    for k, (jid, h) in enumerate(c.execute("SELECT id, hash FROM guardian_journal ORDER BY id"), 1):
                        reg.ajouter("ligne", jid, h, k, "simulation_test", "tests.aide_registre")
    return reg


def recuperer_avec_registre_simule(memoire, confirmation, maintenant=None, registre=None):
    if registre is None:
        try:
            registre = registre_simule(_chemin(memoire))
        except Exception:
            registre = AN.Registre(os.path.join("/nonexistent", "x"))  # lecture impossible -> registre absent
    return _original(memoire, confirmation, maintenant, registre)
