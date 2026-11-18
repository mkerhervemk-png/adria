import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402

import formulaires as F  # noqa: E402
import web as W  # noqa: E402

PDF = os.path.join(os.path.dirname(__file__), "fixtures", "questionnaire_irm.pdf")


class FauxHA:
    async def etat(self, e):
        return "on"


def test_nom_propre():
    assert W.nom_propre("../../etc/passwd") == "passwd.pdf"
    assert W.nom_propre("CONSENTEMENT PATIENT.pdf") == "CONSENTEMENT PATIENT.pdf"


def test_envoi_et_telechargement(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "DOSSIER", str(tmp_path))
    (tmp_path / "sortie").mkdir()
    (tmp_path / "sortie" / "x_rempli.pdf").write_bytes(b"%PDF-1.4 test")
    page = W.PageFormulaires(F.AgentFormulaires(FauxHA()), FauxHA())

    async def scenario():
        runner = web.AppRunner(page.application())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}/"
        async with aiohttp.ClientSession() as s:
            assert "x_rempli" in await (await s.get(base)).text()
            fd = aiohttp.FormData()
            fd.add_field("pdf", open(PDF, "rb"), filename="CONSENTEMENT PATIENT.pdf",
                         content_type="application/pdf")
            t = await (await s.post(base + "envoyer", data=fd)).text()
            assert "ADRIA commence" in t, t[-400:]
            fd = aiohttp.FormData()
            fd.add_field("pdf", b"pas un pdf", filename="a.pdf")
            assert "pas un PDF" in await (await s.post(base + "envoyer", data=fd)).text()
            r = await s.get(base + "fichier/x_rempli.pdf")
            assert r.status == 200 and "attachment" in r.headers["Content-Disposition"]
            assert (await s.get(base + "fichier/..%2Fentree%2Fa.pdf")).status == 404
        await runner.cleanup()

    asyncio.run(scenario())
    assert (tmp_path / "entree" / "CONSENTEMENT PATIENT.pdf").exists()
    assert not (tmp_path / "entree" / "a.pdf").exists()
