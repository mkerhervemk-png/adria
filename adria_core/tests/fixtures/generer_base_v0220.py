"""Génère tests/fixtures/base_v0220.db avec le VRAI code de la v0.22.0 (commit fb018ff), données fictives.

Usage (depuis la racine du dépôt) :
    git archive fb018ff adria_core/app | tar -x -C /tmp/v0220
    python3 -I adria_core/tests/fixtures/generer_base_v0220.py /tmp/v0220/adria_core/app adria_core/tests/fixtures/base_v0220.db

Reproduit ce que la production a fait le 25/11/2026 : démarrage du Core v0.22.0 (le Guardian crée
`guardian_journal`), une mission BRAIN, puis un passage COMPLET du banc (25 scénarios) terminé par
`BANC.garder()` (clé `banc_historique`). Aucun moteur IA réel : un routeur factice répond.
"""
import asyncio
import json
import os
import sqlite3
import sys

app, sortie = sys.argv[1], sys.argv[2]
sys.path.insert(0, app)
import banc as BANC  # noqa: E402
import guardian as GD  # noqa: E402
import main  # noqa: E402

assert main.VERSION == "0.22.0", main.VERSION
if os.path.exists(sortie):
    os.remove(sortie)
main.BASE = sortie
main.lire_options = lambda: {}
c = main.Core()


async def etat(e):
    return {"input_boolean.adria_core_brain": "on", GD.ARRET_URGENCE: "off"}.get(e)


class Routeur:
    async def demander(self, *a, **k):
        return {"ok": True, "moteur": "factice",
                "data": json.dumps({"action": "repondre", "type": "general", "reponse": "Réponse factice."})}


async def appel(methode, chemin, donnees=None, delai=60):
    return [] if chemin == "/states" else {}

c.etat = etat
c.guardian.etat = etat
c.routeur = Routeur()
c.appel = appel


async def go():
    await c.brain.repondre({"question": "Combien font 17 fois 23 ?", "qui": "Mael"})
    res = await BANC.executer(c.brain, c.politique, main.VERSION)
    BANC.garder(c.memoire, res)
    print("banc :", res["reussis"], "/", res["total"], "| journal :", c.guardian.verifier_chaine())

asyncio.run(go())
c.memoire.db.commit()
c.memoire.db.close()
with sqlite3.connect(sortie) as db:
    db.execute("VACUUM")
