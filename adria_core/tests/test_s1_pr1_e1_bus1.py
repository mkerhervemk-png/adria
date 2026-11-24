"""Lots S1 (sens), PR1 (procédures), E1 (erreur → correction), BUS1 (MCP) — v0.17.0."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import mcp as MC  # noqa: E402
import procedures as PR  # noqa: E402
import sens as SE  # noqa: E402
from evolution import Evolution  # noqa: E402
from procedures import Procedures  # noqa: E402
from store import Memoire  # noqa: E402


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


# ------------------------------------------------------------------ S1 : sens
# faux modèle : 3 dimensions « voiture / école / cuisine »
AXES = {"voiture": 0, "5008": 0, "ct": 0, "controle": 0, "inster": 0, "ecole": 1, "maitresse": 1,
        "devoirs": 1, "repas": 2, "courses": 2, "lasagnes": 2}


def faux_vecteur(t):
    v = [0.02] * 3 + [0.0] * 20  # axes 3+ : un axe par mot inconnu (sans recouvrement ici)
    for mot in t.lower().replace(".", " ").split():
        if mot in AXES:
            v[AXES[mot]] += 1
        else:
            v[3 + sum(map(ord, mot)) % 20] += 0.2
    return v


class FauxSens(SE.Sens):
    async def vecteurs(self, textes, delai=120):
        self.appels = getattr(self, "appels", 0) + 1
        return [faux_vecteur(t) for t in textes]


def memoire_test(tmp_path):
    m = Memoire(str(tmp_path / "t.db"))
    m.ajouter("adria_memoire", [{"summary": "CT du 5008 prévu en mars", "date": "2026-10-01"},
                                {"summary": "La maitresse demande les devoirs signés", "date": "2026-10-02"},
                                {"summary": "Lasagnes pour le repas de dimanche", "date": "2026-10-03"}])
    m.ajouter("historique", [{"summary": "10/10 08:00 Salon : allumé", "description": "light.salon",
                              "date": "2026-10-10"}])
    return m


def test_indexation_exclut_historique_et_ne_refait_rien(tmp_path):
    m = memoire_test(tmp_path)
    s = FauxSens(m, "http://x")
    r = run(s.indexer(pause=0))
    assert r["ajoutes"] == 3 and r["a_indexer"] == 0 and not r["erreur"]
    assert run(s.indexer(pause=0))["ajoutes"] == 0


def test_recherche_par_le_sens(tmp_path):
    m = memoire_test(tmp_path)
    s = FauxSens(m, "http://x")
    run(s.indexer(pause=0))
    res = run(s.chercher("contrôle voiture", 3))
    assert res and res[0]["texte"].startswith("CT du 5008") and res[0]["par"] == "sens"
    assert all("Lasagnes" not in r["texte"] for r in res)


def test_rien_au_hasard(tmp_path):
    m = memoire_test(tmp_path)
    s = FauxSens(m, "http://x")
    run(s.indexer(pause=0))
    assert run(s.chercher("météo plage", 3)) == []


def test_sans_numpy_meme_resultat(tmp_path, monkeypatch):
    m = memoire_test(tmp_path)
    s = FauxSens(m, "http://x")
    run(s.indexer(pause=0))
    avec = run(s.chercher("école devoirs", 2))
    monkeypatch.setattr(SE, "np", None)
    s._cache = None
    assert [r["texte"] for r in run(s.chercher("école devoirs", 2))] == [r["texte"] for r in avec]


def test_fusion_mots_dabord_sans_doublon():
    a = [{"texte": "CT du 5008", "source": "x"}]
    b = [{"texte": "CT du 5008", "source": "y"}, {"texte": "Assurance Inster", "source": "y"}]
    assert [l["texte"] for l in SE.fusionner(a, b, 5)] == ["CT du 5008", "Assurance Inster"]


def test_erreur_ollama_ne_casse_rien(tmp_path):
    m = memoire_test(tmp_path)

    class Panne(SE.Sens):
        async def vecteurs(self, textes, delai=120):
            raise ConnectionError("ollama")
    r = run(Panne(m, "http://x").indexer(pause=0))
    assert r["ajoutes"] == 0 and "ConnectionError" in r["erreur"]


# ------------------------------------------------------------------ PR1 : procédures
def test_procedure_dite_a_la_voix(tmp_path):
    p = Procedures(memoire_test(tmp_path))
    r = p.repondre({"mode": "retenir", "qui": "Mael",
                    "texte": "ADRIA retiens comment faire le plein de lave-glace : ouvrir le capot, puis "
                             "dévisser le bouchon bleu, ensuite remplir"})
    assert r["ok"] and "3 étapes" in r["texte"]
    r = p.repondre({"mode": "demander", "texte": "comment on fait le plein de lave-glace ?"})
    assert r["ok"] and "1. ouvrir le capot" in r["texte"]
    assert p.toutes()[0]["utilisations"] == 1


def test_procedure_refusee_pour_les_enfants_et_sans_deux_points(tmp_path):
    p = Procedures(memoire_test(tmp_path))
    assert not p.repondre({"mode": "retenir", "qui": "Iwan", "texte": "retiens comment faire x : a"})["ok"]
    assert not p.repondre({"mode": "retenir", "qui": "Mael", "texte": "retiens comment faire x"})["ok"]
    assert not p.repondre({"texte": "comment on fait les crêpes"})["ok"]


def test_reprise_competences_habitudes_sans_doublon(tmp_path):
    m = memoire_test(tmp_path)
    p = Procedures(m)
    comp = [{"id": "adria_appris_ab12", "alias": "Lave-linge terminé"}]
    lignes = ["Compétence apprise : préviens-moi quand le lave-linge a fini (Notification à la fin)"]
    regles = {"inster": {"titre": "🔌 Inster", "constat": "c", "rappel": "r"}}
    rappels = [{"id": "x1", "regle": "inster", "cle": "nuit", "actif": True},
               {"id": "x2", "regle": "inster", "cle": "jour", "actif": False}]
    assert p.reprendre(comp, lignes, rappels, regles) == 2
    assert p.reprendre(comp, lignes, rappels, regles) == 0
    assert len(p.toutes()) == 2
    # copie dans la mémoire : retrouvable par la recherche par mots
    assert m.chercher("lave-glace") == [] and m.chercher("lave-linge")


def test_retirer_ne_supprime_pas(tmp_path):
    p = Procedures(memoire_test(tmp_path))
    i, _ = p.retenir("test", ["a"], "mael")
    assert p.retirer(i) and p.toutes() == [] and len(p.toutes(actives=False)) == 1


# ------------------------------------------------------------------ E1 : erreur → correction
def test_journal_erreur_correction(tmp_path):
    m = memoire_test(tmp_path)
    e = Evolution(m)
    depart = len(e.lister("corrigée", 100))
    assert depart == len(__import__("evolution").LECONS_DEPART)
    e.suivre("consolidation", "mémoire", False, "IA sans réponse", "ok")
    e.suivre("consolidation", "mémoire", False, "IA sans réponse", "ok")
    ouvertes = e.lister("ouverte")
    assert len(ouvertes) == 1 and ouvertes[0]["fois"] == 2
    e.suivre("consolidation", "mémoire", True, "x", "consolidation réussie (gemini)")
    assert e.lister("ouverte") == []
    assert m.chercher("consolidation réussie")  # la leçon est dans la mémoire
    assert Evolution(m).lister("corrigée", 100).__len__() == depart + 1  # pas de doublon au redémarrage


def test_lecon_de_mael(tmp_path):
    e = Evolution(memoire_test(tmp_path))
    r = e.lecon({"erreur": "Rappel des poubelles le mauvais jour", "correction": "jour corrigé au jeudi"})
    assert r["ok"] and e.etat()["attributes"]["dernieres_corrections"][0].endswith("jour corrigé au jeudi")
    assert not e.lecon({"erreur": ""})["ok"]


def test_aucune_suppression_dans_le_code():
    import re
    for f in ("sens.py", "procedures.py", "evolution.py", "mcp.py"):
        code = open(os.path.join(os.path.dirname(__file__), "..", "app", f), encoding="utf-8").read()
        assert not re.search(r"\b(DELETE\s+FROM|DROP\s+TABLE|TRUNCATE)\b", code, re.I), f


# ------------------------------------------------------------------ BUS1 : MCP
class FauxCore:
    version = "0.17.0"

    def __init__(self, m):
        import confidentialite as CF
        self.memoire = m
        self.procedures = Procedures(m)
        self.evolution = Evolution(m)
        self.politique = CF.Politique(m)

    async def chercher_tout(self, q, n, ex=None, profil="adulte"):
        ex = self.politique.exclusions(profil, ex)
        return self.politique.filtrer(profil, self.memoire.chercher(q, n, ex))

    def resume_etat(self):
        return {"version": self.version}


class Req:
    def __init__(self, remote, auth="", jeton=""):
        self.remote = remote
        self.headers = {"Authorization": auth} if auth else {}
        self.match_info = {"jeton": jeton} if jeton else {}


def test_mcp_acces(tmp_path):
    s = MC.ServeurMCP(FauxCore(memoire_test(tmp_path)), "secret")
    assert s.autorise(Req("172.30.32.1", "Bearer secret"))
    assert s.autorise(Req("172.30.33.5", jeton="secret"))
    assert not s.autorise(Req("172.30.32.1", "Bearer faux"))
    assert not s.autorise(Req("192.168.1.20", "Bearer secret"))  # pas depuis le réseau de la maison
    assert not MC.ServeurMCP(None, "").autorise(Req("127.0.0.1", "Bearer "))


def test_mcp_protocole(tmp_path):
    s = MC.ServeurMCP(FauxCore(memoire_test(tmp_path)), "secret")
    init = run(s.traiter({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-03-26"}}))
    assert init["result"]["protocolVersion"] == "2025-03-26"
    assert run(s.traiter({"jsonrpc": "2.0", "method": "notifications/initialized"})) is None
    noms = {o["name"] for o in run(s.traiter({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))["result"]["tools"]}
    assert {"memoire_chercher", "procedures_chercher", "evolution_journal", "etat_core"} <= noms
    r = run(s.traiter({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                       "params": {"name": "memoire_chercher", "arguments": {"question": "lasagnes"}}}))
    assert "Lasagnes" in json.loads(r["result"]["content"][0]["text"])[0]["texte"]
    r = run(s.traiter({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                       "params": {"name": "evolution_journal", "arguments": {"statut": "corrigée", "n": 2}}}))
    assert len(json.loads(r["result"]["content"][0]["text"])) == 2
    assert run(s.traiter({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                          "params": {"name": "allumer_tout"}}))["error"]["code"] == -32602


def test_mcp_lecture_seule():
    """Aucun outil MCP ne doit pouvoir agir : pas d'appel de service HA dans mcp.py."""
    code = open(os.path.join(os.path.dirname(__file__), "..", "app", "mcp.py"), encoding="utf-8").read()
    assert "/services/" not in code and ".appel(" not in code and "ajouter(" not in code
