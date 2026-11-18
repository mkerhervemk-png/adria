import asyncio
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import courriers as C  # noqa: E402
import formulaires as F  # noqa: E402

J = date(2026, 11, 18)
D1 = (date.today() + timedelta(days=20)).isoformat()
D2 = (date.today() + timedelta(days=40)).isoformat()
D3 = date.today() + timedelta(days=60)


def test_analyser_filtre_et_trie():
    d = {"expediteur": "MAAF Assurances", "objet": "Renouvellement contrat auto",
         "resume": "Votre contrat arrive à échéance.",
         "echeances": [
             {"date": "2026-12-31", "heure": "", "action": "Renvoyer attestation"},
             {"date": "2026-11-01", "action": "date passée"},
             {"date": "pas une date", "action": "x"},
             {"date": "2026-12-01", "heure": "9h30", "action": "RDV agence"},
             {"date": "2026-12-01", "heure": "09:30", "action": "doublon"},
             {"date": "2026-12-20", "action": "a"}, {"date": "2026-12-21", "action": "b"}]}
    c = C.analyser(d, J)
    assert c["expediteur"] == "MAAF Assurances"
    assert [(e["date"].isoformat(), e["heure"]) for e in c["echeances"]] == [
        ("2026-12-01", "09:30"), ("2026-12-20", ""), ("2026-12-21", "")]
    assert C.analyser("n'importe quoi", J)["echeances"] == []
    assert C.analyser({}, J)["expediteur"] == "Courrier"


def test_lire_date():
    assert C.lire_date("25/11", J) == (date(2026, 11, 25), "")
    assert C.lire_date("02/01", J) == (date(2027, 1, 2), "")  # prochaine occurrence
    assert C.lire_date("25-11-26", J) == (date(2026, 11, 25), "")
    assert C.lire_date("25.11.2026 14h30", J) == (date(2026, 11, 25), "14:30")
    assert C.lire_date("25/11 à 9:05", J) == (date(2026, 11, 25), "09:05")
    for faux in ("", "demain", "31/02", "10/11/2026", "25/11 midi"):
        assert C.lire_date(faux, J) is None, faux


def test_evenement():
    c = {"expediteur": "École Saint-Michel", "objet": "Sortie scolaire"}
    jour = C.evenement(c, {"date": date(2026, 12, 3), "heure": "", "action": "Rendre autorisation"}, J)
    assert jour["summary"] == "📬 École Saint-Michel — Rendre autorisation"
    assert jour["start_date"] == "2026-12-03" and jour["end_date"] == "2026-12-04"
    assert jour["description"].startswith(C.MARQUE)
    assert "Copié par ADRIA" not in jour["description"]  # pas confondu avec les copies Famille
    h = C.evenement(c, {"date": date(2026, 12, 3), "heure": "14:30", "action": "RDV"}, J)
    assert h["start_date_time"] == "2026-12-03 14:30:00" and h["end_date_time"] == "2026-12-03 15:00:00"


def test_type_fichier():
    assert C.type_fichier(b"%PDF-1.7") == ".pdf"
    assert C.type_fichier(b"\xff\xd8\xff\xe0abc") == ".jpg"
    assert C.type_fichier(b"\x89PNG\r\n") == ".png"
    assert C.type_fichier(b"RIFF\x00\x00\x00\x00WEBPVP8") == ".webp"
    assert C.type_fichier(b"<html>") is None


def test_rappels_du_jour():
    evs = [{"summary": "📬 MAAF — Payer", "description": C.MARQUE + " le 18/11/2026."},
           {"summary": "Après-midi", "description": "Copié par ADRIA depuis Famille"}]
    assert C.rappels_du_jour(evs) == ["📬 MAAF — Payer"]


class FauxCore:
    def __init__(self, reponses_vision, etat="on"):
        self.vision = reponses_vision
        self.appels, self.notifs = [], []
        self._etat = etat
        self.agent = None

    async def etat(self, e):
        return self._etat

    async def ai_task_vision(self, prompt, tache, medias):
        self.medias = medias
        return self.vision

    async def publier_etat_formulaires(self):
        pass

    async def appel(self, methode, chemin, data=None, delai=60):
        self.appels.append((chemin, data))
        if chemin.startswith("/services/notify"):
            self.notifs.append(data)
            acts = data["data"].get("actions") or []
            if acts and self.agent:
                rep = self.reponses.pop(0)
                act = acts[0]["action"]
                if act == "REPLY":
                    ev = {"action": "REPLY", "reply_text": rep, "tag": "adria_courrier"}
                else:
                    ev = {"action": act.rsplit("_", 1)[0] + "_" + rep}
                asyncio.get_running_loop().call_soon(self.agent.recevoir_action, ev)
        if "get_events" in chemin:
            return {"service_response": {"calendar.mael_adria": {"events": [
                {"summary": "📬 MAAF — Payer", "description": C.MARQUE + " le 18/11/2026."}]}}}
        return {}


def test_traiter_photo_bout_en_bout(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "DOSSIER", str(tmp_path))
    vision = ('```json\n{"expediteur": "Mairie", "objet": "Carte d\'identité", "resume": "Dossier prêt.",'
              ' "categorie": "autre", "pour": "aelia",'
              f' "echeances": [{{"date": "{D1}", "heure": "", "action": "Retirer la carte"}},'
              f' {{"date": "{D2}", "heure": "", "action": "Second"}}]}}\n```')
    import classement as K
    monkeypatch.setattr(K, "RACINE", str(tmp_path / "docs"))
    ha = FauxCore(vision)
    agent = C.AgentCourriers(ha, F.AgentFormulaires(ha, {"prenom": "Mael"}, foyer=[{"prenom": "Aelia"}]),
                             "notify.tel", onedrive="E1")
    ha.agent = agent
    ha.reponses = ["MODIF", D3.strftime("%d/%m/%Y") + " 10h00", "OUI", "NON"]
    entree = tmp_path / "courriers" / "entree"
    entree.mkdir(parents=True)
    photo = entree / "lettre.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0 photo")
    res = asyncio.run(agent.traiter(str(photo)))
    assert res["ok"] and res["echeances"] == 2 and res["ajoutees"] == 1
    assert res["classe"]["dossier"] == "Aelia/Autre"
    assert any(c == "/services/onedrive/upload" for c, _ in ha.appels)
    assert ha.medias[0]["media_content_id"].endswith("/lettre.jpg") is False  # copie dans travail
    assert ha.medias[0]["media_content_type"] == "image/jpeg"
    crees = [d for c, d in ha.appels if c == "/services/calendar/create_event"]
    assert len(crees) == 1
    assert crees[0]["start_date_time"] == D3.isoformat() + " 10:00:00"
    assert crees[0]["entity_id"] == "calendar.mael_adria"
    assert not photo.exists() and list((entree / "traites").iterdir())
    assert "resume" not in res and "Dossier prêt" not in str(res)  # rien de lisible gardé


def test_sans_reponse_rien_ajoute(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "DOSSIER", str(tmp_path))
    monkeypatch.setattr(C, "ATTENTE_REPONSE", 0.05)
    ha = FauxCore('{"expediteur": "X", "echeances": [{"date": "%s", "action": "a"}]}' % D1)
    agent = C.AgentCourriers(ha, F.AgentFormulaires(ha), "notify.tel")
    (tmp_path / "c.png").write_bytes(b"\x89PNG\r\n")
    res = asyncio.run(agent.traiter(str(tmp_path / "c.png")))
    assert res["ok"] and res["ajoutees"] == 0
    assert not [c for c, _ in ha.appels if "create_event" in c]
    assert "rien n'est ajouté" in ha.notifs[-1]["message"]


def test_rappel_j3():
    ha = FauxCore("")
    agent = C.AgentCourriers(ha, F.AgentFormulaires(ha), "notify.tel")
    assert asyncio.run(agent.rappeler(J)) == 1
    get = [d for c, d in ha.appels if "get_events" in c][0]
    assert get["start_date_time"] == "2026-11-21 00:00:00"
    assert "MAAF — Payer" in ha.notifs[-1]["message"]
    ha._etat = "off"
    assert asyncio.run(agent.rappeler(J)) == 0


def test_repli_reply_sans_tag():
    ha = FauxCore("")
    form = F.AgentFormulaires(ha)
    agent = C.AgentCourriers(ha, form, "notify.tel")

    async def scenario():
        fut = asyncio.get_running_loop().create_future()
        agent.attentes["REPLY"] = fut
        agent.recevoir_action({"action": "REPLY", "reply_text": " 25/12 "})
        return await fut
    assert asyncio.run(scenario()) == "25/12"
