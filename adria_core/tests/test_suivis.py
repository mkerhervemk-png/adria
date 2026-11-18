import asyncio
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import courriers as C  # noqa: E402
import formulaires as F  # noqa: E402
import retrouver as RT  # noqa: E402
import suivis as S  # noqa: E402
from store import Memoire  # noqa: E402
from test_courriers import FauxCore  # noqa: E402

J = date(2026, 11, 18)


def facture(montant=39.99, fournisseur="Free", service="box", per="mensuel", jour="2026-11-05", fin=""):
    return {"fournisseur": fournisseur, "service": service, "montant": montant, "periodicite": per,
            "date": jour, "fin_engagement": fin}


# ------------------------------------------------------------------ A4 abonnements
def test_analyser_facture():
    f = S.analyser_facture(facture("44,99 €"))
    assert f["montant"] == 44.99 and f["mois"] == 1 and f["cle"] == "free" and f["service"] == "box"
    assert f["date_doc"] == date(2026, 11, 5)
    assert S.analyser_facture(None) is None
    assert S.analyser_facture(facture(per="unique")) is None  # pas un abonnement
    assert S.analyser_facture(facture(montant="?")) is None
    assert S.analyser_facture(facture(service="Assurance auto"))["service"] == "assurance_auto"
    assert S.analyser_facture(facture(service="bizarre"))["service"] == "autre"
    assert S.libelle_facture(S.analyser_facture(facture(per="annuel", montant=420))) == \
        "Free (box) : 420,00 €/an"


def test_hausse_et_doublon():
    avant = dict(S.analyser_facture(facture(39.99)), date_doc=date(2026, 10, 5))
    apres = S.analyser_facture(facture(44.99))
    assert "39,99 € → 44,99 € (+13 %)" in S.hausse(avant, apres)
    assert S.hausse(avant, S.analyser_facture(facture(40.10))) == ""  # +0,11 € : rien
    assert S.hausse(avant, S.analyser_facture(facture(39.0))) == ""  # baisse
    assert S.hausse(dict(avant, periodicite="annuel"), apres) == ""
    actives = [avant, dict(S.analyser_facture(facture(20, "Netflix", "streaming")), date_doc=J)]
    assert S.doublons(actives, S.analyser_facture(facture(29.99, "Orange"))) == ["Free"]
    assert S.doublons(actives, S.analyser_facture(facture(15, "Bouygues", "mobile"))) == []  # plusieurs lignes normales


def test_actifs_et_rappels():
    vieux = dict(S.analyser_facture(facture(10, "SFR")), date_doc=date(2026, 6, 1))
    an = dict(S.analyser_facture(facture(300, "MAIF", "assurance_auto", "annuel",
                                         fin=(J + timedelta(days=30)).isoformat())), date_doc=date(2026, 1, 10))
    free = dict(S.analyser_facture(facture()), date_doc=date(2026, 11, 5))
    act = S.actifs([vieux, an, free], J)
    assert [f["fournisseur"] for f in act] == ["Free", "MAIF"]  # SFR plus facturé : sorti du total
    assert S.par_mois(an) == 25.0
    g = S.analyser_achat({"appareil": "Lave-linge Bosch", "magasin": "Darty",
                          "date_achat": (J + timedelta(days=30) - timedelta(days=730)).isoformat()}, J)
    msgs = S.rappels([vieux, an, free], [g], J)
    assert len(msgs) == 2 and "MAIF" in msgs[0] and "Lave-linge" in msgs[1]
    assert S.rappels([an], [g], J + timedelta(days=1)) == []


# ------------------------------------------------------------------ A5 garanties
def test_analyser_achat():
    g = S.analyser_achat({"appareil": "Lave-linge Bosch", "magasin": "Darty", "date_achat": "2026-03-12",
                          "garantie_mois": 0}, J)
    assert g["fin"] == date(2028, 3, 12) and g["legale"]
    assert "garantie légale 2 ans" in S.libelle_garantie(g)
    g = S.analyser_achat({"appareil": "TV", "date_achat": "2026-01-31", "garantie_mois": "5"}, J)
    assert g["fin"] == date(2026, 6, 30) and not g["legale"]
    assert S.analyser_achat({"appareil": "TV", "date_achat": "2027-01-01"}, J) is None  # futur
    assert S.analyser_achat({"appareil": "", "date_achat": "2026-01-01"}, J) is None


def test_base_jamais_effacee(tmp_path):
    mem = Memoire(str(tmp_path / "b.db"))
    s = S.Suivis(mem)
    f = S.analyser_facture(facture())
    s.ajouter_facture(f, date(2026, 10, 5))
    s.ajouter_facture(dict(f, montant=44.99), date(2026, 11, 5))
    assert s.precedente("free")["montant"] == 44.99 and len(s.factures()) == 2
    s.ajouter_garantie(S.analyser_achat({"appareil": "Four", "date_achat": "2026-02-01"}, J), "Foyer", "Foyer/Achats")
    e = s.etat(J)
    assert e["state"] == 44.99 and e["attributes"]["garanties"][0]["appareil"] == "Four"
    assert mem.chercher("garantie four", 5)  # la voix retrouve la garantie
    code = open(os.path.join(os.path.dirname(__file__), "..", "app", "suivis.py")).read()
    assert not re.search(r"\b(DELETE\s+FROM|DROP\s+TABLE|TRUNCATE)\b", code, re.I)


def _agent(tmp_path, monkeypatch, vision, reponses):
    monkeypatch.setattr(F, "DOSSIER", str(tmp_path))
    import classement as K
    monkeypatch.setattr(K, "RACINE", str(tmp_path / "docs"))
    ha = FauxCore(vision)
    agent = C.AgentCourriers(ha, F.AgentFormulaires(ha, {"prenom": "Mael"}), "notify.tel")
    agent.suivis = S.Suivis(Memoire(str(tmp_path / "b.db")))
    ha.agent, ha.reponses = agent, reponses
    (tmp_path / "f.jpg").write_bytes(b"\xff\xd8\xff\xe0 photo")
    return ha, agent


def test_courrier_facture_hausse_puis_oui(tmp_path, monkeypatch):
    vision = json.dumps({"expediteur": "Free", "objet": "Facture novembre", "resume": "Facture.",
                         "categorie": "facture", "echeances": [], "facture": facture(44.99), "achat": None})
    ha, agent = _agent(tmp_path, monkeypatch, vision, ["OUI"])
    agent.suivis.ajouter_facture(dict(S.analyser_facture(facture(39.99))), date(2026, 10, 5))
    res = asyncio.run(agent.traiter(str(tmp_path / "f.jpg")))
    assert res["ok"] and res["facture"] == "suivie" and res.get("hausse")
    question = [n for n in ha.notifs if n["title"] == "💶 Suivre cet abonnement ?"][0]
    assert "+13 %" in question["message"]
    assert res["classe"]["dossier"] == "Foyer/Factures"
    assert agent.suivis.precedente("free")["montant"] == 44.99


def test_courrier_ticket_non_rien_garde(tmp_path, monkeypatch):
    vision = json.dumps({"expediteur": "Darty", "objet": "Achat lave-linge", "categorie": "achat",
                         "echeances": [], "facture": None,
                         "achat": {"appareil": "Lave-linge Bosch", "magasin": "Darty",
                                   "date_achat": "2026-11-10", "garantie_mois": 0}})
    ha, agent = _agent(tmp_path, monkeypatch, vision, ["NON"])
    res = asyncio.run(agent.traiter(str(tmp_path / "f.jpg")))
    assert res["garantie"] == "non" and agent.suivis.garanties() == []
    ha._etat = "off"  # interrupteur éteint : aucune question
    (tmp_path / "f.jpg").write_bytes(b"\xff\xd8\xff\xe0 photo")
    res = asyncio.run(agent.traiter(str(tmp_path / "f.jpg")))
    assert "garantie" not in res


# ------------------------------------------------------------------ A6 sors-moi le papier
def _docs(tmp_path):
    for rel in ("Foyer/Assurance/2025-12-20_MAIF_Attestation_assurance_Hyundai_Inster.pdf",
                "Foyer/Assurance/2026-11-02_MAIF_Attestation_assurance_Hyundai_Inster.pdf",
                "Foyer/Assurance/2026-11-02_MAIF_Attestation_assurance_Peugeot_5008.pdf",
                "Aelia/École/2026-10-01_Saint-Michel_Fiche_de_renseignements.pdf",
                "Foyer/Factures/2026-11-05_Free_Facture_novembre.jpg"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4")
    (tmp_path / "Foyer" / "Factures" / "notes.txt").write_text("x")
    return str(tmp_path)


def test_chercher(tmp_path):
    r = _docs(tmp_path)
    t = RT.chercher("Envoie-moi l'attestation d'assurance de l'Inster", r)
    assert t[0]["chemin"].startswith("Foyer/Assurance/2026-11-02") and "Inster" in t[0]["titre"]
    assert len(t) == 2  # 5008 écarté (Inster absent)
    assert RT.chercher("sors-moi la dernière facture Free", r)[0]["type"] == "Factures"
    assert RT.chercher("fiche école Aelia", r)[0]["personne"] == "Aelia"
    assert RT.chercher("envoie-moi le papier", r) == []  # aucun mot utile : rien au hasard
    assert RT.chercher("attestation mutuelle", r) == []


def test_chemin_sur(tmp_path):
    r = _docs(tmp_path)
    assert RT.chemin_sur("Foyer/Factures/2026-11-05_Free_Facture_novembre.jpg", r)
    assert RT.chemin_sur("../../etc/passwd", r) is None
    assert RT.chemin_sur("Foyer/Factures/notes.txt", r) is None


def test_demande_voix(tmp_path, monkeypatch):
    monkeypatch.setattr(RT, "RACINE", _docs(tmp_path / "docs"))
    monkeypatch.setattr(F, "DOSSIER", str(tmp_path / "f"))
    monkeypatch.setattr(RT, "apercu", lambda s, d: False)
    ha = FauxCore("")
    rt = RT.Retrouver(ha, F.AgentFormulaires(ha))
    res = asyncio.run(rt.demander({"question": "attestation assurance Inster", "req_id": "r1"}))
    assert res["ok"] and res["req_id"] == "r1" and "Inster" in res["texte"]
    assert ha.notifs[-1]["title"] == "📄 Papier trouvé" and "1 autre" in ha.notifs[-1]["message"]
    res = asyncio.run(rt.demander({"question": "carte grise"}))
    assert not res["ok"] and len(ha.notifs) == 1  # rien envoyé
    ha._etat = "off"
    assert not asyncio.run(rt.demander({"question": "attestation Inster"}))["ok"]
