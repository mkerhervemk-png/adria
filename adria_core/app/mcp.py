"""Bus MCP du Core (lot BUS1, 19/11/2026) : le Core expose ses outils au protocole MCP.

- Transport « Streamable HTTP » (JSON-RPC 2.0 en POST, réponse JSON), port 8100, adresse interne
  http://0daf054b-adria-core:8100/mcp — joignable seulement depuis le réseau interne de HA
  (Home Assistant, ses applis) et avec le jeton `mcp_jeton` (en-tête « Authorization: Bearer … »
  ou dans l'adresse : /mcp/<jeton>). Sans jeton configuré, le serveur ne démarre pas.
- LECTURE SEULE : aucun outil n'agit sur la maison ni n'écrit dans la mémoire. Les agents (ADRIA dans
  HA, Claude…) peuvent consulter la mémoire, les procédures, le journal des erreurs et l'état du Core ;
  toute action passe toujours par HA et par l'accord de Mael.
- Chaque appel est noté dans le journal du Core (outil, durée, ok).
"""

import ipaddress
import json
import logging
import secrets
import time

from aiohttp import web

LOG = logging.getLogger("adria_core.mcp")

PORT = 8100
VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
RESEAUX_OK = [ipaddress.ip_network(n) for n in ("172.30.32.0/23", "127.0.0.0/8")]

OUTILS = [
    {"name": "memoire_chercher",
     "description": "Cherche dans la mémoire longue d'ADRIA (tout l'historique du foyer) : d'abord par mots, "
                    "puis par le sens. Renvoie des lignes datées avec leur source. Rien au hasard : liste vide "
                    "si rien ne ressemble assez.",
     "inputSchema": {"type": "object", "properties": {
         "question": {"type": "string", "description": "Question ou sujet, en français"},
         "n": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5}}, "required": ["question"]}},
    {"name": "memoire_periode",
     "description": "Ce qui s'est passé entre deux dates (AAAA-MM-JJ), éventuellement filtré par mots.",
     "inputSchema": {"type": "object", "properties": {
         "debut": {"type": "string"}, "fin": {"type": "string"}, "mots": {"type": "string"},
         "n": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10}}, "required": ["debut", "fin"]}},
    {"name": "procedures_chercher",
     "description": "Mémoire procédurale : comment faire une tâche (étapes retenues par Mael, compétences "
                    "apprises, rappels d'habitude). Sans question : liste toutes les procédures actives.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}}},
    {"name": "evolution_journal",
     "description": "Journal « erreur → correction » d'ADRIA : problèmes ouverts et leçons déjà corrigées.",
     "inputSchema": {"type": "object", "properties": {
         "statut": {"type": "string", "enum": ["ouverte", "corrigée"]},
         "n": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}}}},
    {"name": "carnet_dernier",
     "description": "Dernier carnet de bord du soir d'ADRIA (résumé de la journée + réflexion).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "habitudes",
     "description": "Habitudes repérées dans l'historique (avec leur statut) et rappels acceptés par Mael.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "etat_core",
     "description": "État d'ADRIA Core : version, mémoire, recherche par le sens, dernière recopie, purge, "
                    "consolidation, maintenance, moteurs IA sur 24 h.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def _texte(obj) -> dict:
    t = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": t[:20000]}], "isError": False}


class ServeurMCP:
    def __init__(self, core, jeton: str):
        self.core = core
        self.jeton = jeton
        self.appels = 0

    # ------------------------------------------------------------------ accès
    def autorise(self, request) -> bool:
        try:
            ip = ipaddress.ip_address(request.remote or "")
        except ValueError:
            return False
        if not any(ip in r for r in RESEAUX_OK):
            return False
        entete = request.headers.get("Authorization", "")
        donne = entete[7:] if entete.lower().startswith("bearer ") else request.match_info.get("jeton", "")
        return bool(self.jeton) and secrets.compare_digest(donne, self.jeton)

    def application(self) -> web.Application:
        app = web.Application(client_max_size=1024 * 1024)
        app.add_routes([web.post("/mcp", self.post), web.post("/mcp/{jeton}", self.post),
                        web.get("/mcp", self.get), web.get("/mcp/{jeton}", self.get),
                        web.delete("/mcp", self.get), web.delete("/mcp/{jeton}", self.get)])
        return app

    async def demarrer(self) -> None:
        runner = web.AppRunner(self.application(), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", PORT).start()
        LOG.info("Bus MCP en écoute sur le port %s (lecture seule)", PORT)

    async def get(self, request):
        if not self.autorise(request):
            raise web.HTTPUnauthorized()
        return web.Response(status=405, headers={"Allow": "POST"})  # pas de flux serveur -> client

    async def post(self, request):
        if not self.autorise(request):
            LOG.warning("MCP : accès refusé depuis %s", request.remote)
            raise web.HTTPUnauthorized()
        try:
            corps = await request.json()
        except Exception:
            return web.json_response({"jsonrpc": "2.0", "id": None,
                                      "error": {"code": -32700, "message": "JSON invalide"}}, status=400)
        lot = corps if isinstance(corps, list) else [corps]
        reponses = [r for r in [await self.traiter(m) for m in lot] if r is not None]
        if not reponses:
            return web.Response(status=202)
        return web.json_response(reponses if isinstance(corps, list) else reponses[0],
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False))

    # ------------------------------------------------------------------ JSON-RPC
    async def traiter(self, m: dict) -> dict | None:
        if not isinstance(m, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "requête invalide"}}
        methode, ident, params = m.get("method"), m.get("id"), m.get("params") or {}
        if ident is None:  # notification (notifications/initialized…) : pas de réponse
            return None

        def ok(res):
            return {"jsonrpc": "2.0", "id": ident, "result": res}

        if methode == "initialize":
            demandee = params.get("protocolVersion")
            return ok({"protocolVersion": demandee if demandee in VERSIONS else VERSIONS[0],
                       "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": {"name": "adria-core", "version": self.core.version},
                       "instructions": "Mémoire et état d'ADRIA (foyer Kerherve). Lecture seule."})
        if methode == "ping":
            return ok({})
        if methode == "tools/list":
            return ok({"tools": OUTILS})
        if methode == "tools/call":
            t0 = time.monotonic()
            nom = params.get("name")
            try:
                res = _texte(await self.outil(nom, params.get("arguments") or {}))
            except KeyError:
                return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": f"outil inconnu : {nom}"}}
            except Exception as e:
                LOG.warning("MCP %s : %s", nom, str(e)[:150])
                res = {"content": [{"type": "text", "text": f"Erreur : {str(e)[:200]}"}], "isError": True}
            self.appels += 1
            LOG.info("MCP %s (%.1f s, %s)", nom, time.monotonic() - t0, "erreur" if res["isError"] else "ok")
            return ok(res)
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"méthode inconnue : {methode}"}}

    async def outil(self, nom: str, a: dict):
        c = self.core
        if nom == "memoire_chercher":
            return await c.chercher_tout(str(a.get("question") or ""), int(a.get("n") or 5), [])
        if nom == "memoire_periode":
            from store import mots_cles
            return c.memoire.chercher_periode(str(a["debut"]), str(a["fin"]), mots_cles(str(a.get("mots") or "")),
                                              max(1, min(int(a.get("n") or 10), 20)), [])
        if nom == "procedures_chercher":
            q = str(a.get("question") or "").strip()
            return c.procedures.chercher(q, 5) if q else c.procedures.toutes()
        if nom == "evolution_journal":
            return c.evolution.lister(a.get("statut"), int(a.get("n") or 10))
        if nom == "carnet_dernier":
            return c.agent_memoire.dernier or "Pas encore de carnet."
        if nom == "habitudes":
            return {"reperees": getattr(c.habitudes, "derniers", []), "rappels": c.habitudes.rappels}
        if nom == "etat_core":
            return c.resume_etat()
        raise KeyError(nom)
