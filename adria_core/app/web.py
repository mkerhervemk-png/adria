"""Page « ADRIA Formulaires » (Ingress HA) : envoyer un PDF, récupérer les PDF remplis.

Les Médias de HA refusent les PDF à l'import : cette page les accepte. Elle n'est joignable
que par Home Assistant (Ingress, 172.30.32.2), donc derrière la connexion HA.
"""

import html
import os
import re
from datetime import datetime
from urllib.parse import unquote

from aiohttp import web

import formulaires as F

INGRESS_IP = "172.30.32.2"
PORT = 8099
NOM_SUR = re.compile(r"^[\w\-. ]+$")

STYLE = """
body{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#111318;color:#e6e6e6}
h1{font-size:1.3em;margin:0 0 12px}h2{font-size:1.05em;margin:24px 0 8px;color:#9fb3c8}
.carte{background:#1c1f26;border-radius:12px;padding:14px;margin-bottom:12px}
input[type=file]{width:100%;padding:12px 0;color:#e6e6e6}
button,.bouton{display:inline-block;background:#3b82f6;color:#fff;border:0;border-radius:10px;
padding:12px 18px;font-size:1em;text-decoration:none;margin-top:6px}
.gris{color:#8a93a0;font-size:.9em}img{width:100%;border-radius:8px;margin-top:8px;background:#fff}
.ok{color:#4ade80}.ko{color:#f87171}
"""


def page(corps: str, message: str = "") -> web.Response:
    return web.Response(content_type="text/html", text=(
        "<!doctype html><html lang=fr><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>ADRIA Formulaires</title><style>{STYLE}</style><body>"
        f"<h1>📝 ADRIA Formulaires</h1>{message}{corps}</body></html>"))


def nom_propre(nom: str) -> str:
    base = os.path.basename(unquote(nom or ""))
    base = re.sub(r"[^\w\-. ]+", "_", base).strip(" .") or "formulaire.pdf"
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base[:100]


def lister_sorties(dossier: str, n: int = 15) -> list[str]:
    try:
        return sorted((f for f in os.listdir(dossier) if f.endswith(".pdf")),
                      key=lambda f: os.path.getmtime(os.path.join(dossier, f)), reverse=True)[:n]
    except OSError:
        return []


class PageFormulaires:
    def __init__(self, agent: F.AgentFormulaires, ha) -> None:
        self.agent = agent
        self.ha = ha

    @web.middleware
    async def seulement_ha(self, request, handler):
        if request.remote not in (INGRESS_IP, "127.0.0.1"):
            raise web.HTTPForbidden()
        return await handler(request)

    def application(self) -> web.Application:
        app = web.Application(client_max_size=25 * 1024 * 1024, middlewares=[self.seulement_ha])
        app.add_routes([web.get("/", self.accueil), web.post("/envoyer", self.envoyer),
                        web.get("/fichier/{nom}", self.fichier)])
        return app

    async def accueil(self, request, message: str = "") -> web.Response:
        actif = await self.ha.etat("input_boolean.adria_core_formulaires") == "on"
        attente = sorted(f for f in os.listdir(os.path.join(F.DOSSIER, "entree"))
                         if f.lower().endswith(".pdf")) if os.path.isdir(os.path.join(F.DOSSIER, "entree")) else []
        etat = ""
        if not actif:
            etat = "<p class=ko>Interrupteur « ADRIA Core formulaires » éteint : rien ne sera traité.</p>"
        elif self.agent.en_cours:
            etat = (f"<p>⏳ En cours : {html.escape(self.agent.en_cours)} — réponds aux notifications "
                    "sur ton téléphone.</p>")
        if attente:
            etat += f"<p class=gris>En attente : {html.escape(', '.join(attente))}</p>"
        d = self.agent.dernier
        if d and not d.get("ok"):
            etat += f"<p class=ko>Dernier essai raté ({html.escape(d.get('fichier', ''))}) : {html.escape(d.get('erreur', ''))}</p>"
        envoi = ("<div class=carte><form method=post action=envoyer enctype=multipart/form-data>"
                 "<input type=file name=pdf accept='application/pdf,.pdf' required>"
                 "<button>Envoyer à ADRIA</button></form>"
                 "<p class=gris>ADRIA remplit ce qu'elle sait (nom, date…) et te pose le reste par "
                 "notification. Rien de sensible n'est enregistré. Elle ne signe pas et n'envoie rien.</p>"
                 f"{etat}</div>")
        sortie = os.path.join(F.DOSSIER, "sortie")
        cartes = []
        for f in lister_sorties(sortie):
            b = f[:-4]
            quand = datetime.fromtimestamp(os.path.getmtime(os.path.join(sortie, f))).strftime("%d/%m %H:%M")
            img = (f"<img src='fichier/{html.escape(b)}.png' alt='aperçu'>"
                   if os.path.exists(os.path.join(sortie, b + ".png")) else "")
            cartes.append(f"<div class=carte><b>{html.escape(b)}</b><br><span class=gris>{quand}</span>"
                          f"{img}<br><a class=bouton href='fichier/{html.escape(f)}' download>"
                          "Télécharger le PDF</a></div>")
        liste = "<h2>Formulaires remplis</h2>" + ("".join(cartes) or "<p class=gris>Aucun pour l'instant.</p>")
        return page(envoi + liste, message)

    async def envoyer(self, request) -> web.Response:
        lecteur = await request.multipart()
        champ = await lecteur.next()
        if champ is None or champ.name != "pdf" or not champ.filename:
            return await self.accueil(request, "<p class=ko>Aucun fichier reçu.</p>")
        donnees = await champ.read(decode=False)
        if not donnees.startswith(b"%PDF"):
            return await self.accueil(request, "<p class=ko>Ce fichier n'est pas un PDF.</p>")
        entree = os.path.join(F.DOSSIER, "entree")
        os.makedirs(entree, exist_ok=True)
        nom = nom_propre(champ.filename)
        chemin = os.path.join(entree, nom)
        if os.path.exists(chemin):
            nom = datetime.now().strftime("%H%M%S_") + nom
            chemin = os.path.join(entree, nom)
        with open(chemin, "wb") as fh:
            fh.write(donnees)
        msg = (f"<p class=ok>✅ {html.escape(nom)} reçu. ADRIA commence dans moins d'une minute : "
               "garde ton téléphone à portée pour les questions.</p>")
        return await self.accueil(request, msg)

    async def fichier(self, request) -> web.StreamResponse:
        nom = request.match_info["nom"]
        if not NOM_SUR.match(nom) or not nom.endswith((".pdf", ".png")):
            raise web.HTTPNotFound()
        chemin = os.path.join(F.DOSSIER, "sortie", nom)
        if not os.path.isfile(chemin):
            raise web.HTTPNotFound()
        entetes = {"Cache-Control": "no-store"}
        if nom.endswith(".pdf"):
            entetes["Content-Disposition"] = f'attachment; filename="{nom}"'
        return web.FileResponse(chemin, headers=entetes)

    async def demarrer(self) -> None:
        runner = web.AppRunner(self.application())
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
