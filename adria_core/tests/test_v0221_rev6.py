"""v0.22.1 (révision 6) — registre d'ancrage : complétude du journal du Guardian à partir d'une date connue.

Chaque vérification relit les fichiers et la base APRÈS coup (connexion SQLite neuve, octets du registre).
"""

import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime

import pytest

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

import ancrage as AN  # noqa: E402
import banc as BANC  # noqa: E402
import guardian as GD  # noqa: E402
from store import Memoire  # noqa: E402

J = datetime(2026, 11, 25, 18, 0)
DEMAIN = datetime(2026, 11, 26, 9, 0)
OK, RECO, NOUV = BANC.CONFIRMATION, BANC.RECONNAISSANCE, BANC.NOUVELLE_AUTORISATION
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "base_v0220.db")
HISTO = {"quand": "2026-11-25T17:42:29", "version": "0.22.0", "moteur": "chaîne", "score": 88, "reussis": 22,
         "total": 25, "par_categorie": {}, "latence_moyenne": 28.0}


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


async def off(e):
    return "off"


def decisions(m, reg, n, mission="m"):
    gd = GD.Guardian(m, off, reg)
    for i in range(n):
        assert run(gd.decider("memoire_chercher" if i % 2 else "etat_core", "adulte", mission=f"{mission}{i}")).autorise
    return gd


def base_ancree(tmp_path, n=6, historique=True):
    """Base 0.22.1 ancrée DEPUIS SA CRÉATION (le Core crée journal et registre dès le démarrage)."""
    chemin, reg = str(tmp_path / "core.db"), AN.Registre(str(tmp_path / "ancres"))
    m = Memoire(chemin)
    decisions(m, reg, n)
    if historique:
        BANC.garder(m, HISTO)  # le banc a déjà servi
    return chemin, m, reg


def instantane(chemin, reg):
    with sqlite3.connect(chemin) as c:
        meta = dict(c.execute("SELECT cle, valeur FROM meta WHERE cle IN ('banc_initialise', 'banc_compte')"))
        journal = [tuple(r) for r in c.execute("SELECT * FROM guardian_journal ORDER BY id")] \
            if c.execute("SELECT 1 FROM sqlite_master WHERE name = 'guardian_journal'").fetchone() else None
    octets = ("<dossier>" if os.path.isdir(reg.chemin) else open(reg.chemin, "rb").read()) \
        if os.path.exists(reg.chemin) else None
    return meta, journal, octets


def etat(chemin, reg):
    with sqlite3.connect(chemin) as c:
        return reg.verifier(c)


def tronquer_journal(chemin, garder_jusqua):
    """Le cas DANGEREUX : on retire la fin, la chaîne interne restante reste valide."""
    with sqlite3.connect(chemin) as c:
        c.execute("DELETE FROM guardian_journal WHERE id > ?", (garder_jusqua,))


def refus_sans_effet(chemin, m, reg, *motifs, fn=None):
    avant = instantane(chemin, reg)
    ok, motif = (fn or (lambda: BANC.recuperer(m, OK, J, reg)))()
    assert not ok and all(x in motif for x in motifs), motif
    assert instantane(chemin, reg) == avant  # journal, registre (octets), compteur, marqueur : inchangés
    return motif


# ===================================================================== complétude et troncature
def test_journal_complet_et_ancre_correspondante(tmp_path):
    chemin, m, reg = base_ancree(tmp_path)
    v = etat(chemin, reg)
    assert v["etat"] == AN.COMPLET and v["garantie_depuis"]
    lignes = [json.loads(x) for x in open(reg.chemin)]
    assert [e["seq"] for e in lignes] == list(range(1, 7)) and [e["jid"] for e in lignes] == list(range(1, 7))
    assert all(e["origine"] == "guardian" and e["source"] == "guardian.noter" for e in lignes)


@pytest.mark.parametrize("garder", [5, 3, 1], ids=["derniere", "plusieurs", "presque_tout"])
def test_troncature_finale_chaine_restante_valide(tmp_path, garder):
    chemin, m, reg = base_ancree(tmp_path)
    tronquer_journal(chemin, garder)
    assert BANC._etat_journal(m.db) == "integre"  # la chaîne interne seule ne voit RIEN…
    v = etat(chemin, reg)
    assert v["etat"] == AN.TRONQUE  # …le registre, si
    refus_sans_effet(chemin, m, reg, "complétude du journal non démontrée", "tronque", "incohérence")
    refus_sans_effet(chemin, m, reg, "incohérence prouvée", fn=lambda: BANC.recuperer_non_verifiee(m, OK, RECO, J, reg))


def test_base_restauree_depuis_une_ancienne_sauvegarde(tmp_path):
    chemin, m, reg = base_ancree(tmp_path, n=3)
    m.db.close()
    shutil.copyfile(chemin, chemin + ".sauvegarde")  # sauvegarde partielle de l'appli (/data seulement)
    m = Memoire(chemin)
    decisions(m, reg, 4, "apres")  # la vie continue, tout est ancré
    m.db.close()
    shutil.copyfile(chemin + ".sauvegarde", chemin)  # restauration de l'ancienne base ; /media non restauré
    m = Memoire(chemin)
    BANC.garder(m, HISTO)
    assert BANC._etat_journal(m.db) == "integre" and etat(chemin, reg)["etat"] == AN.TRONQUE
    refus_sans_effet(chemin, m, reg, "tronque")


def test_registre_restaure_dans_un_etat_ancien(tmp_path):
    chemin, m, reg = base_ancree(tmp_path, n=3)
    shutil.copyfile(reg.chemin, reg.chemin + ".ancien")
    decisions(m, reg, 3, "apres")
    shutil.copyfile(reg.chemin + ".ancien", reg.chemin)  # registre revenu en arrière
    v = etat(chemin, reg)
    assert v["etat"] == AN.EN_RETARD and "3 ligne(s)" in v["motif"]
    refus_sans_effet(chemin, m, reg, "en_retard")
    # et s'il reprend après ce retour en arrière : la reprise saute des lignes -> lacune, jamais « complet »
    decisions(m, reg, 1, "reprise")
    assert etat(chemin, reg)["etat"] == AN.LACUNE
    refus_sans_effet(chemin, m, reg, "lacune")


def test_hash_terminal_modifie(tmp_path):
    chemin, m, reg = base_ancree(tmp_path)
    lignes = open(reg.chemin).read().splitlines()
    e = json.loads(lignes[-1])
    e["jhash"] = "f" * 64
    open(reg.chemin, "w").write("\n".join(lignes[:-1] + [json.dumps(e, sort_keys=True)]) + "\n")
    assert etat(chemin, reg)["etat"] == AN.CORROMPU  # la chaîne du registre détecte la retouche
    refus_sans_effet(chemin, m, reg, "corrompu")
    e["h"] = AN.empreinte(e["prec"], e)  # retouche « soignée » : chaîne du registre recalculée
    open(reg.chemin, "w").write("\n".join(lignes[:-1] + [json.dumps(e, sort_keys=True)]) + "\n")
    assert etat(chemin, reg)["etat"] == AN.DIVERGENT  # le hash ne correspond plus au journal
    refus_sans_effet(chemin, m, reg, "divergent")


def test_hash_du_journal_modifie_chaine_recalculee(tmp_path):
    chemin, m, reg = base_ancree(tmp_path)
    with sqlite3.connect(chemin) as c:  # dernière ligne réécrite avec un hash recalculé (chaîne interne valide)
        r = c.execute("SELECT id, quand, mission, profil, outil, classe, autorise, raison, prec FROM guardian_journal"
                      " ORDER BY id DESC LIMIT 1").fetchone()
        contenu = dict(zip(("quand", "mission", "profil", "outil", "classe", "autorise", "raison"), r[1:8]))
        contenu["raison"] = "retouche"
        c.execute("UPDATE guardian_journal SET raison = 'retouche', hash = ? WHERE id = ?",
                  (GD.empreinte(r[8], contenu), r[0]))
    assert BANC._etat_journal(m.db) == "integre" and etat(chemin, reg)["etat"] == AN.DIVERGENT
    refus_sans_effet(chemin, m, reg, "ne correspond plus")


def test_compteur_incoherent(tmp_path):
    chemin, m, reg = base_ancree(tmp_path)
    lignes = [json.loads(x) for x in open(reg.chemin)]
    lignes[2]["n"] = 99  # chaîne du registre recalculée : seul le compteur est faux
    prec = AN.ZERO
    for e in lignes:
        e["prec"] = prec
        e["h"] = AN.empreinte(prec, e)
        prec = e["h"]
    open(reg.chemin, "w").write("".join(json.dumps(e, sort_keys=True) + "\n" for e in lignes))
    v = etat(chemin, reg)
    assert v["etat"] == AN.DIVERGENT and "compte incohérent" in v["motif"]
    refus_sans_effet(chemin, m, reg, "compte incohérent")


@pytest.mark.parametrize("cas", ["absent", "vide", "inaccessible"])
def test_registre_absent_vide_inaccessible(tmp_path, cas):
    chemin, m, reg = base_ancree(tmp_path)
    if cas == "absent":
        os.remove(reg.chemin)
        attendu = AN.ABSENT
    elif cas == "vide":
        open(reg.chemin, "w").close()
        attendu = AN.VIDE
    else:
        os.remove(reg.chemin)
        os.mkdir(reg.chemin)  # le « fichier » est un dossier : lecture impossible
        attendu = AN.INACCESSIBLE
    assert etat(chemin, reg)["etat"] == attendu
    refus_sans_effet(chemin, m, reg, attendu)
    assert not os.path.isfile(reg.chemin) or os.path.getsize(reg.chemin) == 0  # rien n'est reconstruit


def test_registre_dossier_indisponible(tmp_path):
    chemin, m, _ = base_ancree(tmp_path)
    open(str(tmp_path / "pas_un_dossier"), "w").close()
    reg = AN.Registre(str(tmp_path / "pas_un_dossier" / "ancres"))  # makedirs impossible
    ok, motif = BANC.recuperer(m, OK, J, reg)
    assert not ok and "inaccessible" in motif
    assert not BANC.recuperer(m, OK, J, None)[0]  # sans registre : jamais d'autorisation


def test_ligne_jsonl_partielle(tmp_path):
    chemin, m, reg = base_ancree(tmp_path)
    with open(reg.chemin, "ab") as f:
        f.write(b'{"v": 1, "seq": 7, "ty')  # écriture interrompue
    octets = open(reg.chemin, "rb").read()
    assert etat(chemin, reg)["etat"] == AN.CORROMPU
    gd = decisions(m, reg, 1, "apres")  # le Guardian continue de décider…
    assert gd.ancrage_en_retard  # …sans prétendre ancrer
    assert open(reg.chemin, "rb").read() == octets  # la ligne partielle n'est ni complétée ni réparée
    refus_sans_effet(chemin, m, reg, "ligne partielle")


@pytest.mark.parametrize("defaut", ["doublon", "recul", "trou"])
def test_numerotation_du_registre(tmp_path, defaut):
    chemin, m, reg = base_ancree(tmp_path)
    lignes = open(reg.chemin).read().splitlines(keepends=True)
    if defaut == "doublon":
        lignes.append(lignes[-1])
    elif defaut == "recul":
        lignes = lignes[:3] + [lignes[1]] + lignes[3:]
    else:
        del lignes[2]
    open(reg.chemin, "w").write("".join(lignes))
    assert etat(chemin, reg)["etat"] == AN.CORROMPU
    refus_sans_effet(chemin, m, reg, "corrompu")


# ===================================================================== échecs d'ancrage
def test_ecriture_sqlite_reussie_puis_echec_du_registre(tmp_path, monkeypatch):
    chemin, m, reg = base_ancree(tmp_path, n=3)
    original = AN.Registre.ajouter

    def panne(self, *a, **k):
        raise OSError("disque /media plein")
    monkeypatch.setattr(AN.Registre, "ajouter", panne)
    gd = GD.Guardian(m, off, reg)
    d = run(gd.decider("memoire_chercher", "adulte", mission="x"))
    assert d.autorise and gd.ancrage_en_retard  # la décision journalisée reste valable
    with sqlite3.connect(chemin) as c:
        assert c.execute("SELECT COUNT(*) FROM guardian_journal").fetchone()[0] == 4  # bien journalisée
    assert len(open(reg.chemin).read().splitlines()) == 3  # jamais prétendue ancrée
    assert etat(chemin, reg)["etat"] == AN.EN_RETARD
    monkeypatch.setattr(AN.Registre, "ajouter", original)
    decisions(m, reg, 1, "apres")  # la panne passée, l'état dégradé reste visible (lacune), sans réparation
    assert etat(chemin, reg)["etat"] == AN.LACUNE
    refus_sans_effet(chemin, m, reg, "lacune", "migration NON vérifiée")


ARRET = r'''
import sys, os, asyncio
sys.path.insert(0, sys.argv[1])
import ancrage as AN, guardian as GD
from store import Memoire
async def off(e): return "off"
def coupure(*a, **k): os._exit(9)       # arrêt brutal APRÈS le commit SQLite, AVANT l'ancrage
GD.ancrer = coupure
gd = GD.Guardian(Memoire(sys.argv[2]), off, AN.Registre(sys.argv[3]))
asyncio.run(gd.decider("etat_core", "adulte", mission="z"))
'''


def test_arret_brutal_entre_les_deux_ecritures(tmp_path):
    chemin, m, reg = base_ancree(tmp_path, n=3)
    m.db.close()
    r = subprocess.run([sys.executable, "-I", "-c", ARRET, APP, chemin, reg.dossier], capture_output=True)
    assert r.returncode == 9, r.stderr
    with sqlite3.connect(chemin) as c:
        assert c.execute("SELECT COUNT(*) FROM guardian_journal").fetchone()[0] == 4
    assert etat(chemin, reg)["etat"] == AN.EN_RETARD  # détecté, pas de complétude prétendue
    m = Memoire(chemin)
    refus_sans_effet(chemin, m, reg, "en_retard")


def test_ecritures_concurrentes(tmp_path):
    chemin, reg = str(tmp_path / "c.db"), AN.Registre(str(tmp_path / "ancres"))
    GD.Guardian(Memoire(chemin), off, reg).memoire.db.close()
    depart = threading.Barrier(8)

    def fil(i):
        m = Memoire(chemin)
        gd = GD.Guardian(m, off, reg)
        depart.wait()
        for k in range(5):
            run(gd.decider("etat_core", "adulte", mission=f"{i}-{k}"))
            if k == 2:
                BANC.reserver(m, J, registre=reg)  # le banc écrit aussi, depuis une autre connexion
        m.db.close()
    fils = [threading.Thread(target=fil, args=(i,)) for i in range(8)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    with sqlite3.connect(chemin) as c:
        n = c.execute("SELECT COUNT(*) FROM guardian_journal").fetchone()[0]
    assert n == 8 * 5 + 2  # 40 décisions + 2 réservations accordées (quota)
    v = etat(chemin, reg)
    assert v["etat"] == AN.COMPLET, v
    assert [json.loads(x)["jid"] for x in open(reg.chemin)] == list(range(1, n + 1))


# ===================================================================== migration non vérifiée (production v0.22.0)
def base_v0220(tmp_path):
    chemin = str(tmp_path / "v0220.db")
    shutil.copyfile(FIXTURE, chemin)
    return chemin, Memoire(chemin), AN.Registre(str(tmp_path / "ancres"))


def test_migration_v0220_explicitement_non_verifiee(tmp_path):
    chemin, m, reg = base_v0220(tmp_path)
    assert etat(chemin, reg)["etat"] == AN.ABSENT
    refus_sans_effet(chemin, m, reg, "absent", "migration NON vérifiée")  # la voie normale refuse
    with sqlite3.connect(chemin) as c:
        avant = c.execute("SELECT id, hash FROM guardian_journal ORDER BY id DESC LIMIT 1").fetchone()
    ok, motif = BANC.recuperer_non_verifiee(m, OK, RECO, J, reg)
    assert ok and "NON démontrée" in motif and "2026-11-25T18:00:00" in motif
    meta, journal, _ = instantane(chemin, reg)
    assert json.loads(meta["banc_initialise"])["origine"] == "recuperation_non_verifiee"
    assert json.loads(meta["banc_compte"]) == {"date": "2026-11-25", "n": 2, "v": 2}  # quota du jour épuisé
    assert journal[-1][4] == "banc_recuperation_non_verifiee" and "NON démontrée" in journal[-1][7]
    assert journal[-2][0] == avant[0] and journal[-2][9] == avant[1]  # historique passé NON réécrit
    ref = [json.loads(x) for x in open(reg.chemin)]
    assert len(ref) == 1 and ref[0]["type"] == "reference" and ref[0]["origine"] == "recuperation_non_verifiee"
    assert ref[0]["observe"]["jid"] == avant[0] and ref[0]["observe"]["jhash"] == avant[1]
    assert ref[0]["observe"]["n"] == len(journal) - 1 and "NON démontrée" in ref[0]["note"]
    assert ref[0]["jid"] == journal[-1][0]  # la référence ancre la ligne de migration elle-même
    v = etat(chemin, reg)
    assert v["etat"] == AN.COMPLET_REF and v["garantie_depuis"] == "2026-11-25T18:00:00"
    # à partir de la référence, ancrage normal
    assert not BANC.reserver(m, J, registre=reg)
    assert BANC.reserver_detail(m, DEMAIN, registre=reg) == (True, "passage 1/2")
    decisions(m, reg, 2, "apres")
    assert etat(chemin, reg)["etat"] == AN.COMPLET_REF
    # une troncature APRÈS la référence est détectée…
    tronquer_journal(chemin, journal[-1][0] + 1)
    assert etat(chemin, reg)["etat"] == AN.TRONQUE


def test_migration_detecte_une_suppression_avant_la_reference(tmp_path):
    chemin, m, reg = base_v0220(tmp_path)
    assert BANC.recuperer_non_verifiee(m, OK, RECO, J, reg)[0]
    with sqlite3.connect(chemin) as c:
        c.execute("DELETE FROM guardian_journal WHERE id = 1")  # l'historique d'avant ne peut plus changer
    assert etat(chemin, reg)["etat"] == AN.DIVERGENT


def test_deuxieme_migration_refusee_sans_nouvelle_autorisation(tmp_path):
    chemin, m, reg = base_v0220(tmp_path)
    assert BANC.recuperer_non_verifiee(m, OK, RECO, J, reg)[0]
    refus_sans_effet(chemin, m, reg, "pas dans le cas E",
                     fn=lambda: BANC.recuperer_non_verifiee(m, OK, RECO, J, reg))
    # le compteur et le marqueur sont perdus plus tard, et une ligne n'a pas pu être ancrée (lacune)
    with sqlite3.connect(chemin) as c:
        c.execute("DELETE FROM meta WHERE cle IN ('banc_compte', 'banc_initialise')")
    GD.Guardian(m, off, None)
    run(GD.Guardian(m, off, None).decider("etat_core", "adulte"))  # ligne non ancrée
    refus_sans_effet(chemin, m, reg, "référence non vérifiée existe déjà", "nouvelle autorisation",
                     fn=lambda: BANC.recuperer_non_verifiee(m, OK, RECO, J, reg))
    refus_sans_effet(chemin, m, reg, "référence non vérifiée existe déjà",
                     fn=lambda: BANC.recuperer_non_verifiee(m, OK, RECO, J, reg, "oui"))
    ok, _ = BANC.recuperer_non_verifiee(m, OK, RECO, DEMAIN, reg, NOUV)
    assert ok and sum(json.loads(x)["type"] == "reference" for x in open(reg.chemin)) == 2


def test_migration_refusee_si_completude_demontree(tmp_path):
    chemin, m, reg = base_ancree(tmp_path)
    refus_sans_effet(chemin, m, reg, "complétude est démontrée",
                     fn=lambda: BANC.recuperer_non_verifiee(m, OK, RECO, J, reg))
    ok, _ = BANC.recuperer(m, OK, J, reg)  # la voie normale, vérifiée, fonctionne
    assert ok and etat(chemin, reg)["etat"] == AN.COMPLET


@pytest.mark.parametrize("phrases", [("", ""), (OK, ""), ("", RECO), (OK, RECO.lower()), (RECO, OK)])
def test_migration_exige_deux_phrases_exactes(tmp_path, phrases):
    chemin, m, reg = base_v0220(tmp_path)
    refus_sans_effet(chemin, m, reg, "deux phrases exactes",
                     fn=lambda: BANC.recuperer_non_verifiee(m, phrases[0], phrases[1], J, reg))


def test_migration_jamais_declenchable_par_un_agent_ou_le_modele():
    import brain as BR
    import mcp as MC
    interdits = ("recuperer", "RECONNAISSANCE", "NOUVELLE_AUTORISATION")
    for nom in os.listdir(APP):
        if nom.endswith(".py") and nom != "banc.py":
            code = open(os.path.join(APP, nom), encoding="utf-8").read()
            assert not any(x in code for x in interdits), nom
    assert not any("recuper" in o["name"] for o in MC.OUTILS)
    assert not any("recuper" in o for o in BR.OUTILS) and not any("recuper" in o for o in GD.REGISTRE)
    main = open(os.path.join(APP, "main.py"), encoding="utf-8").read()
    assert "adria_core_banc_recuper" not in main and "migration" not in main.lower().split("ecouter")[-1][:4000]


# ===================================================================== non-régression A à D et autonomie
SCENARIOS_AD = [
    ("A vierge", []),
    ("B ancien compteur", [("banc_compte", '{"date": "2026-11-25", "n": 1}')]),
    ("C marqueur seul", [("banc_initialise", '{"v": 1, "cree": "2026-11-20T10:00:00", "origine": "neuve"}')]),
    ("D corrompu", [("banc_compte", "{cassé"), ("banc_initialise",
                                               '{"v": 1, "cree": "2026-11-20T10:00:00", "origine": "neuve"}')]),
]


@pytest.mark.parametrize("nom,meta", SCENARIOS_AD, ids=[s[0] for s in SCENARIOS_AD])
def test_cas_A_a_D_inchanges_avec_registre(tmp_path, nom, meta):
    resultats = []
    for avec in (False, True):
        chemin = str(tmp_path / f"{avec}.db")
        m = Memoire(chemin)
        for cle, val in meta:
            m.db.execute("INSERT INTO meta (cle, valeur) VALUES (?, ?)", (cle, val))
        m.db.commit()
        reg = AN.Registre(str(tmp_path / f"ancres_{avec}")) if avec else None
        resultats.append([BANC.reserver_detail(m, J, registre=reg) for _ in range(3)])
        with sqlite3.connect(chemin) as c:
            resultats[-1].append(dict(c.execute("SELECT cle, valeur FROM meta")))
    assert resultats[0] == resultats[1]


def test_guardian_et_brain_fonctionnent_registre_indisponible(tmp_path):
    from test_ascension import faire
    c = faire(tmp_path, [{"action": "outil", "outil": "etat_core", "args": {}},
                         {"action": "repondre", "type": "maison", "reponse": "Version 0.22.0.", "preuves": ["O1"]}])
    open(str(tmp_path / "fichier"), "w").close()
    c.guardian.registre = AN.Registre(str(tmp_path / "fichier" / "ancres"))  # /media indisponible
    r = run(c.brain.repondre({"question": "Quelle version ?", "qui": "Mael"}))
    assert r["ok"] and r["statut"] == "sait" and c.guardian.ancrage_en_retard
    assert c.guardian.verifier_chaine()["ok"]
    assert c.brain.etat()["attributes"]["ancrage"]["ancrage_en_retard"] is True
    assert not run(c.brain.repondre({"question": "Désarme l'alarme", "qui": "Mael"}))["ok"]  # refus intact


def test_sonde(tmp_path):
    assert AN.Registre(str(tmp_path / "a")).sonde()["accessible"]
    open(str(tmp_path / "f"), "w").close()
    s = AN.Registre(str(tmp_path / "f" / "a")).sonde()
    assert not s["accessible"] and s["echec"]
