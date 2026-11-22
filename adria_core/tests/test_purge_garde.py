"""Garde-fou de purge : aucune suppression sans input_boolean.adria_core_purge ON."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import main  # noqa: E402

LISTE = main.LISTES_PURGE[0]


class FauxCore:
    """Remplace HA : enregistre les appels, ne touche à rien."""

    def __init__(self, interrupteur):
        self.interrupteur = interrupteur
        self.appels = []
        self.suivis = []
        self.derniere_purge = None
        self.evolution = type("E", (), {"suivre": lambda _s, *a: self.suivis.append(a)})()

    async def etat(self, entity_id):
        assert entity_id == "input_boolean.adria_core_purge"
        return None if isinstance(self.interrupteur, Exception) else self.interrupteur

    async def appel(self, methode, chemin, donnees=None, delai=60):
        self.appels.append(chemin)
        if "get_items" in chemin:
            return {"service_response": {LISTE: {"items": [{"uid": "u1", "summary": "x"}]}}}
        return {}

    def _choisir(self, liste, source, items, maintenant):
        return items, items  # tout est purgeable et déjà copié

    async def publier_etat(self):
        pass

    async def synchroniser(self):
        pass

    async def reprendre_procedures(self):
        pass

    async def indexer_sens(self):
        pass

    habitudes = type("H", (), {"calculer": lambda s: None, "publier": staticmethod(lambda: asyncio.sleep(0))})()

    async def purger(self, data=None):
        return await main.Core.purger(self, data)


def lancer(interrupteur, data=None):
    f = FauxCore(interrupteur)
    res = asyncio.run(main.Core.purger(f, data))
    return f, res


def suppressions(f):
    return [c for c in f.appels if c.endswith("remove_item")]


def test_off_evenement_normal_bloque_sans_aucun_appel_ha():
    f, res = lancer("off")
    assert res["bloquee"] is True and res["retirees"] == {}
    assert [c for c in f.appels if "todo" in c] == []
    assert f.suivis == []  # pas compté comme une purge en erreur
    assert "adria_core_purge_resultat" in f.appels[-1]  # le résultat est publié


def test_off_essai_simulation_autorisee_sans_suppression():
    f, res = lancer("off", {"essai": "true"})
    assert res["bloquee"] is False and res["essai"] is True
    assert suppressions(f) == []
    assert res["retirees"]  # la simulation compte bien les lignes


def test_on_evenement_normal_comportement_conserve():
    f, res = lancer("on")
    assert res["bloquee"] is False
    assert suppressions(f) == ["/services/todo/remove_item"]  # seule la 1re liste a des lignes
    assert res["retirees"] == {LISTE.removeprefix("todo."): 1}
    assert len(f.suivis) == 1


def test_erreur_de_lecture_ferme_le_garde_fou():
    f, res = lancer(RuntimeError("HA injoignable"))
    assert res["bloquee"] is True
    assert suppressions(f) == []


def test_etat_inattendu_ferme_le_garde_fou():
    for etat in ("unavailable", "unknown", "ON", ""):
        f, res = lancer(etat)
        assert res["bloquee"] is True, etat
        assert suppressions(f) == []


def test_aucun_parametre_forcer():
    f, res = lancer("off", {"forcer": "true"})
    assert res["bloquee"] is True
    assert suppressions(f) == []


def test_parcours_nocturne_on_purge():
    f = FauxCore("on")
    asyncio.run(main.Core.nuit(f))
    assert suppressions(f)


def test_parcours_nocturne_off_ne_purge_pas():
    f = FauxCore("off")
    asyncio.run(main.Core.nuit(f))
    assert suppressions(f) == []
