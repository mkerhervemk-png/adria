import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import classement as K  # noqa: E402


def test_categorie():
    assert K.categorie("École") == "École"
    assert K.categorie("sante") == "Santé"
    assert K.categorie("IMPOTS") == "Impôts"
    assert K.categorie("ecole primaire") == "École"
    assert K.categorie("bidule") == "Autre"
    assert K.categorie(None) == "Autre"


def test_destination_sans_ecrasement(tmp_path):
    d = K.destination("Aelia", "ecole", date(2026, 11, 18), "Fiche de renseignements", ".PDF", str(tmp_path))
    assert d == str(tmp_path / "Aelia" / "École" / "2026-11-18_Fiche_de_renseignements.pdf")
    os.makedirs(os.path.dirname(d))
    open(d, "w").close()
    d2 = K.destination("Aelia", "ecole", date(2026, 11, 18), "Fiche de renseignements", ".pdf", str(tmp_path))
    assert d2.endswith("_2.pdf")
    assert "/../" not in K.destination("../../etc", "x", date(2026, 1, 1), "../a", ".pdf", str(tmp_path))


class FauxHA:
    def __init__(self, etat="on", panne=False):
        self._etat, self.panne, self.appels = etat, panne, []

    async def etat(self, e):
        return self._etat

    async def appel(self, m, chemin, d=None, delai=60):
        self.appels.append((chemin, d))
        if self.panne:
            raise RuntimeError("OneDrive KO")
        return {}


def test_classer(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "RACINE", str(tmp_path / "docs"))
    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF")
    assert asyncio.run(K.classer(FauxHA("off"), str(src), "Mael", "sante", "IRM", "E1")) is None
    ha = FauxHA()
    r = asyncio.run(K.classer(ha, str(src), "Mael", "sante", "IRM", "E1", date(2026, 11, 18)))
    assert r == {"dossier": "Mael/Santé", "onedrive": "ok"}
    assert (tmp_path / "docs" / "Mael" / "Santé" / "2026-11-18_IRM.pdf").exists() and src.exists()
    chemin, d = ha.appels[0]
    assert chemin == "/services/onedrive/upload" and d["destination_folder"] == "ADRIA/documents/Mael/Santé"
    r = asyncio.run(K.classer(FauxHA(panne=True), str(src), "Mael", "sante", "IRM", "E1", date(2026, 11, 18)))
    assert r["onedrive"] == "échec" and (tmp_path / "docs" / "Mael" / "Santé" / "2026-11-18_IRM_2.pdf").exists()
