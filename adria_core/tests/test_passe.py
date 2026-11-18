import asyncio
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import passe as P  # noqa: E402
from store import Memoire  # noqa: E402


def test_lignes_papiers(tmp_path):
    d = tmp_path / "Aelia" / "École"
    d.mkdir(parents=True)
    (d / "2026-10-14_Ecole_Saint-Michel_Reunion_parents.pdf").write_bytes(b"%PDF")
    (d / "2026-10-14_Ecole_Saint-Michel_Reunion_parents_2.pdf").write_bytes(b"%PDF")
    (d / "pas_de_date.pdf").write_bytes(b"x")
    l = P.lignes_papiers(str(tmp_path))
    assert len(l) == 2 and l[0]["date"] == "2026-10-14"
    assert l[0]["summary"] == "Papier École pour Aelia : Ecole Saint-Michel Reunion parents"
    assert P.lignes_papiers(str(tmp_path / "absent")) == []


def test_lignes_agenda():
    evs = [{"summary": "Pneus Inster chez Norauto", "start": "2026-03-12T09:30:00+01:00", "location": "Langueux"},
           {"summary": "Réunion école", "start": "2026-10-14"}, {"summary": "", "start": "2026-10-14"},
           {"summary": "x", "start": "pas une date"}]
    l = P.lignes_agenda(evs)
    assert [i["date"] for i in l] == ["2026-03-12", "2026-10-14"]
    assert l[0]["summary"] == "12/03/2026 09:30 Pneus Inster chez Norauto (Langueux)"


def test_lignes_savoir_date_du_fait():
    l = P.lignes_savoir([{"summary": "Mael a changé les pneus de l'Inster en mars"},
                         {"summary": "Iwan aime les pâtes"}], date(2026, 11, 18))
    assert l[0]["date"] == "2026-03-01" and l[1]["date"] == "2026-11-18"


def test_recherche_quand_et_periode():
    m = Memoire(":memory:")
    m.ajouter("agenda", P.lignes_agenda([{"summary": "Pneus Inster chez Norauto", "start": "2026-03-12"}]))
    m.ajouter("papiers", [{"date": "2026-10-14", "summary": "Papier École pour Aelia : Ecole Saint-Michel sortie"}])
    r = m.chercher("Quand ai-je changé les pneus ?")
    assert r and "Pneus" in r[0]["texte"] and r[0]["date"] == "2026-03-12"
    r = m.chercher("Qu'avait dit l'école en octobre ?", auj=date(2026, 11, 18))
    assert r and r[0]["source"] == "papiers"
    m.ajouter("adria_savoir", [{"summary": "[regle] rappeler le changement d heure"}])
    assert m.chercher("Quand ai-je changé les pneus de la 5008 ?") == [] or \
        all("pneu" in x["texte"].lower() for x in m.chercher("Quand ai-je changé les pneus de la 5008 ?"))
    assert m.chercher("Quand ai-je changé le filtre ?") == []  # « changé » seul ne suffit pas
    m.ajouter("agenda", P.lignes_agenda([{"summary": "Cours Aelia (école)", "start": f"2026-10-0{j}"} for j in (1, 2, 5)]))
    r = m.chercher("Qu'avait dit l'école en octobre ?", auj=date(2026, 11, 18))
    assert "papiers" in [x["source"] for x in r] and sum("Cours" in x["texte"] for x in r) == 1
    assert P.libelle("papiers") == "papier classé" and P.libelle("archive/souvenir") == "souvenir"


class FauxCore:
    def __init__(self, etat="on"):
        self._etat = etat
        self.memoire = Memoire(":memory:")

    async def etat(self, e):
        return self._etat

    async def appel(self, m, chemin, d=None, delai=60):
        if chemin == "/states":
            return [{"entity_id": "calendar.famille_2", "attributes": {"friendly_name": "Famille"}},
                    {"entity_id": "calendar.vacances_scolaires_zone_b", "attributes": {}},
                    {"entity_id": "input_datetime.5008_date_derniere_revision",
                     "attributes": {"friendly_name": "Date dernière révision 5008"}}]
        if "calendar/get_events" in chemin:
            assert d["entity_id"] == "calendar.famille_2"
            return {"service_response": {"calendar.famille_2": {"events": [
                {"summary": "Dentiste Iwan", "start": "2026-09-02T17:00:00+02:00"}]}}}
        if "todo/get_items" in chemin:
            return {"service_response": {"todo.adria_savoir": {"items": [{"summary": "[vehicule] pneus 5008 changés en mars"}]}}}
        if chemin.startswith("/history/period"):
            return [[{"entity_id": "input_datetime.5008_date_derniere_revision", "state": "2026-11-18",
                      "last_changed": "2026-11-18T09:00:00+00:00"}]]
        raise AssertionError(chemin)


def test_import_complet_et_interrupteur(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "RACINE_PAPIERS", str(tmp_path))
    c = FauxCore("off")
    r = asyncio.run(P.AgentPasse(c).importer(datetime(2026, 11, 18, 3, 30)))
    assert r["actif"] is False and c.memoire.stats()["lignes"] == 0
    c = FauxCore()
    r = asyncio.run(P.AgentPasse(c).importer(datetime(2026, 11, 18, 3, 30)))
    assert r["erreurs"] == [] and r["nouvelles"] == {"papiers": 0, "agenda": 1, "adria_savoir": 1, "historique": 1}
    assert c.memoire.meta("passe_agenda_jusqu_a") == "2026-11-18"
    r = asyncio.run(P.AgentPasse(c).importer(datetime(2026, 11, 19, 3, 30)))
    assert sum(r["nouvelles"].values()) == 0  # rien en double
