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


def test_envoi_courrier_photo(tmp_path, monkeypatch):
    import courriers as C
    monkeypatch.setattr(F, "DOSSIER", str(tmp_path))
    ha = FauxHA()
    form = F.AgentFormulaires(ha)
    page = W.PageFormulaires(form, ha, C.AgentCourriers(ha, form, "notify.tel"))

    async def scenario():
        runner = web.AppRunner(page.application())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}/"
        async with aiohttp.ClientSession() as s:
            assert "Courrier, facture ou ticket" in await (await s.get(base)).text()
            fd = aiohttp.FormData()
            fd.add_field("mode", "courrier")
            fd.add_field("pdf", b"\xff\xd8\xff\xe0 photo", filename="IMG_2026.jpeg", content_type="image/jpeg")
            assert "ADRIA commence" in await (await s.post(base + "envoyer", data=fd)).text()
            fd = aiohttp.FormData()
            fd.add_field("mode", "formulaire")
            fd.add_field("pdf", b"\xff\xd8\xff\xe0 photo", filename="b.jpg")
            assert "pas un PDF" in await (await s.post(base + "envoyer", data=fd)).text()
            fd = aiohttp.FormData()
            fd.add_field("mode", "courrier")
            fd.add_field("pdf", b"<html>", filename="c.jpg")
            assert "photo" in await (await s.post(base + "envoyer", data=fd)).text()
        await runner.cleanup()

    asyncio.run(scenario())
    assert (tmp_path / "courriers" / "entree" / "IMG_2026.jpg").exists()
    assert not (tmp_path / "entree").exists() or not list((tmp_path / "entree").glob("*.jpg"))


def test_envoi_pour_un_membre_du_foyer(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "DOSSIER", str(tmp_path))
    ha = FauxHA()
    form = F.AgentFormulaires(ha, {"prenom": "Mael"}, foyer=[{"prenom": "Aelia"}])
    page = W.PageFormulaires(form, ha)

    async def scenario():
        runner = web.AppRunner(page.application())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{port}/"
        async with aiohttp.ClientSession() as s:
            assert "Pour qui" in await (await s.get(base)).text()
            for pour in ("1", "1", "0", "99"):
                fd = aiohttp.FormData()
                fd.add_field("pour", pour)
                fd.add_field("pdf", open(PDF, "rb"), filename="fiche.pdf", content_type="application/pdf")
                assert "ADRIA commence" in await (await s.post(base + "envoyer", data=fd)).text()
        await runner.cleanup()

    asyncio.run(scenario())
    noms = sorted(os.listdir(tmp_path / "entree"))
    assert "@Aelia@fiche.pdf" in noms
    assert any(n.startswith("@Aelia@") and n != "@Aelia@fiche.pdf" for n in noms)  # pas d'écrasement
    assert sum(1 for n in noms if not n.startswith("@")) == 2  # 0 et 99 -> Mael
    assert all(F.PREFIXE_POUR.match(n) or not n.startswith("@") for n in noms)


def test_retrouver_un_papier(tmp_path, monkeypatch):
    import retrouver as RT
    monkeypatch.setattr(F, "DOSSIER", str(tmp_path / "f"))
    docs = tmp_path / "docs" / "Foyer" / "Assurance"
    docs.mkdir(parents=True)
    (docs / "2026-11-02_MAIF_Attestation_Inster.pdf").write_bytes(b"%PDF-1.4 att")
    monkeypatch.setattr(RT, "RACINE", str(tmp_path / "docs"))
    page = W.PageFormulaires(F.AgentFormulaires(FauxHA()), FauxHA(), None, RT.Retrouver(FauxHA(), None))

    async def scenario():
        runner = web.AppRunner(page.application())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/"
        async with aiohttp.ClientSession() as s:
            assert "Retrouver un papier" in await (await s.get(base)).text()
            t = await (await s.get(base + "chercher", params={"q": "attestation inster"})).text()
            assert "MAIF Attestation Inster" in t and "doc?c=Foyer/Assurance/" in t
            r = await s.get(base + "doc", params={"c": "Foyer/Assurance/2026-11-02_MAIF_Attestation_Inster.pdf"})
            assert r.status == 200 and await r.read() == b"%PDF-1.4 att"
            assert (await s.get(base + "doc", params={"c": "../../etc/passwd"})).status == 404
        await runner.cleanup()

    asyncio.run(scenario())
