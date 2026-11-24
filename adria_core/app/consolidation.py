"""Lot M2b-2 (18/11) : consolidation de nuit de todo.adria_memoire par le Core.

Reprise à l'identique de script.adria_memoire_consolider (v3.5) :
tri 0 IA (RES > 30 j, OBS oubliées, OBS n>=5 -> REGLE), saut si rien de nouveau,
un seul appel IA « rapide », lecture imp|tag|n|dt|fait, garde-fou 50 %.
Essai (18/11) : rien n'est écrit dans HA, la proposition est gardée (meta).
Réel (v0.18.0, 23/11, validé par Mael) : si input_boolean.adria_core_consolidation_reelle est ON,
le Core écrit dans todo.adria_memoire exactement comme le script HA (les anciens faits passent en
« terminé », jamais effacés sans copie dans la base du Core ; les nouveaux faits sont ajoutés).
Le script HA de 02h30 ne tourne plus que si le Core n'a pas réussi cette nuit (secours).
v0.21.2 (R2) : plus de contournement par forcer=true ; préparation complète avant écriture ;
interdits contrôlés par confidentialite.sujet_interdit (synonymes, reformulations) — R6.
"""
import json
import logging
import re
from datetime import datetime, timedelta

import confidentialite as CF
from memoire_agent import items as liste_items

LOG = logging.getLogger("adria_core.consolidation")
LISTE = "todo.adria_memoire"
MAITRE = "input_boolean.adria_core_consolidation"
REEL = "input_boolean.adria_core_consolidation_reelle"

COURTES = ["input_text.avatar_memoire_apprentissages", "input_text.avatar_memoire_patterns",
           "input_text.adria_mem_decisions", "input_text.adria_raisons_refus_recents",
           "input_text.adria_mem_vie"]

CONTEXTE = {  # clé du prompt -> entité (même ordre que le script HA)
    "APPRENTISSAGES": "input_text.avatar_memoire_apprentissages",
    "PATTERNS": "input_text.avatar_memoire_patterns",
    "ACTIONS": "input_text.avatar_memoire_actions",
    "DOMICILE": "input_text.adria_mem_domicile",
    "VIE": "input_text.adria_mem_vie",
    "DECISIONS": "input_text.adria_mem_decisions",
    "SUCCES": "input_text.adria_patterns_succes",
    "REFUS": "input_text.adria_raisons_refus_recents",
    "PROFIL": "input_text.avatar_profil_mael",
    "FAMILLE": "input_text.avatar_famille",
    "PROFILS_APPRIS": "input_text.avatar_profils_famille_apprentis",
    "S0": "input_text.avatar_memoire_semaine_s0",
    "S1": "input_text.avatar_memoire_semaine_s1",
    "ECRANS_SEMAINE": "input_text.avatar_media_hebdo_synthese",
    "ALEXA_SEMAINE": "input_text.avatar_alexa_hebdo_synthese",
    "VOIX_PIECES_SEMAINE": "input_text.adria_semaine_pieces",
    "VOIX_SANS_EFFET": "input_text.adria_voix_frictions",
    "CAL_ECOLE": "input_text.adria_cal_ecole",
    "TYPE_DE_JOUR": "input_text.adria_v6_predictions",
    "PROFIL_JOUR_MALADE": "input_text.adria_v7_profil_jour_malade",
}

ENTETE = (
    "Tu consolides la MEMOIRE LONGUE d'ADRIA (IA de la maison Kerherve, Saint-Brieuc; Mael travaille en "
    "quarts 4/8). Date: {date}.\n"
    "REGLES: fusionne les doublons; si contradiction garde le plus recent; supprime le transitoire (etat "
    "ponctuel, meteo du jour, erreurs techniques resolues) sauf si c'est un apprentissage durable; conserve "
    "TOUT ce qui concerne les preferences, habitudes, refus, decisions de Mael et de la famille; max 40 "
    "faits; chaque fait <=120 caracteres, factuel, en francais.\n"
    "INTERDITS (decision de Mael le 13/10, ne JAMAIS recreer, reformuler ni deduire): purge quotidienne de "
    "la base de donnees ; Mael convalescent ou veille sur Aelia ; preference pour l hygiene ou la "
    "maintenance proactive ; Nuit Douce / Nuit Economique / charge 21h30 ; rendez-vous medicaux ; la Ruche "
    "; backlog AUTO_JAM ; faux positifs cour = vehicules uniquement ; climatisation Inster stop auto ; "
    "priorisation via ADE ; patterns voix top semaine. FAIT FIXE: jours malades Mael+Vanessa confirmes par "
    "Mael jusqu au 30/10/2026, reprise des quarts le 31/10.\n"
    "IMPORTANCE: 5=identite/regle permanente, 4=preference forte ou refus, 3=habitude, 2=observation, "
    "1=faible.\n"
    "TAGS autorises: profil, famille, preference, refus, habitude, maison, energie, inster, securite, media, "
    "travail, sante_maison, decision, saison, eclairage, ecole, calendrier, regle, obs, res, pb. Integre les "
    "habitudes ecrans (decodeurs Orange, Fire TV, TV LG), enceintes/voix Alexa et eclairage en faits tag "
    "media/eclairage/habitude, distincts jour de travail / jour malade.\n"
    "MEMOIRE v3.1: n=K dans la description = fait vu K fois (plus K est grand, plus il est durable). Si >=5 "
    "decisions/refus identiques sur au moins 3 jours -> UN seul fait tag regle imp 5 (sinon garde obs) "
    "(regle apprise, prioritaire). Observation non confirmee -> tag obs imp 2 (supprime si > 14 jours sans "
    "repetition). Ignore les actions ponctuelles (CMD, TTS, PBx, snapshot OK). MEMOIRE v3.4: une REGLE "
    "contredite par >= 2 faits [RES] [ko] du meme domaine redevient obs imp 2. MEMOIRE v3.2: classes regle "
    "(regle apprise), pb (probleme recurrent), res (resultat d action ok/ko/annule: conserve tel quel, tag "
    "res), obs. Garde (n=K) a la fin du fait quand il est connu.\n"
    "FORMAT SORTIE STRICT: une ligne par fait: imp|tag|n|dt|fait (n = nombre de fois vu = somme des n "
    "fusionnes, 1 si inconnu ; dt = date AAAA-MM-JJ la plus ANCIENNE des faits fusionnes, date du jour si "
    "nouveau). Aucune autre ligne, aucun commentaire.\n\n"
)


def _dt(desc: str) -> str:
    m = re.search(r"dt=(\d{4}-\d{2}-\d{2})", desc or "")
    return m.group(1) if m else "9999-99-99"


def _n(item: dict) -> list[int]:
    return [int(x) for x in re.findall(r"n=(\d+)", f"{item.get('description') or ''} {item.get('summary') or ''}")]


def trier(items: list[dict], maintenant: datetime) -> dict:
    """Tri déterministe 0 IA, identique au script HA. Renvoie les uid concernés."""
    j = lambda d: (maintenant - timedelta(days=d)).strftime("%Y-%m-%d")  # noqa: E731
    res_vieux, obs_vieilles, promouvoir = [], [], []
    for i in items:
        s, d, uid = (i.get("summary") or ""), (i.get("description") or ""), i.get("uid")
        bas, dt, nn = s.lower(), _dt(d), _n(i)
        if bas.startswith("[res]") and dt < j(30):
            res_vieux.append(uid)
        if bas.startswith("[obs]") and nn and max(nn) >= 5 and dt <= j(2):
            promouvoir.append({"uid": uid, "s": "[REGLE]" + s[5:],
                               "d": re.sub(r"imp=\d", "imp=5", d) + ";promu=" + maintenant.strftime("%Y-%m-%d")})
        m = max(nn) if nn else 1
        if bas.startswith("[obs]") and ((m <= 1 and dt < j(14)) or (m < 5 and dt < j(30))):
            obs_vieilles.append(uid)
    return {"res_vieux": res_vieux, "obs_vieilles": obs_vieilles, "promouvoir": promouvoir}


def appliquer_tri(items: list[dict], tri: dict) -> list[dict]:
    """Ce que verrait le script HA après son tri (sans rien écrire)."""
    retires = set(tri["res_vieux"]) | set(tri["obs_vieilles"])
    promus = {p["uid"]: p for p in tri["promouvoir"]}
    res = []
    for i in items:
        if i.get("uid") in retires:
            continue
        p = promus.get(i.get("uid"))
        res.append({**i, "summary": p["s"], "description": p["d"]} if p else i)
    return res


def rien_de_nouveau(items: list[dict], courtes_changees: int) -> bool:
    nouveaux = sum(1 for i in items if "src=consolide" not in (i.get("description") or ""))
    return nouveaux == 0 and courtes_changees == 0


def construire_prompt(items: list[dict], ctx: dict, maintenant: datetime) -> str:
    brut = "".join(f"{i.get('summary') or ''} ({i.get('description') or ''})\n" for i in items)
    c = lambda k: ctx.get(k) or ""  # noqa: E731
    return (ENTETE.format(date=maintenant.strftime("%Y-%m-%d"))
            + f"MEMOIRE ACTUELLE ({len(items)} faits):\n{brut}"
            + "MEMOIRES COURTES A INTEGRER:\n"
            + "".join(f"{k}: {c(k)}\n" for k in ("APPRENTISSAGES", "PATTERNS", "ACTIONS", "DOMICILE", "VIE",
                                                  "DECISIONS", "SUCCES", "REFUS"))
            + f"PROFIL: {c('PROFIL')} | FAMILLE: {c('FAMILLE')} | PROFILS_APPRIS: {c('PROFILS_APPRIS')}\n"
            + f"SEMAINES: S0={c('S0')} | S1={c('S1')} \n"
            + f"ECRANS_SEMAINE: {c('ECRANS_SEMAINE')}\nALEXA_SEMAINE: {c('ALEXA_SEMAINE')}\n"
            + f"VOIX_PIECES_SEMAINE: {c('VOIX_PIECES_SEMAINE')} | VOIX_SANS_EFFET: {c('VOIX_SANS_EFFET')}\n"
            + f"CAL_ECOLE: {c('CAL_ECOLE')}\n"
            + f"TYPE_DE_JOUR (S travail/ecole, W week-end, R repos, X malade): {c('TYPE_DE_JOUR')[:60]}\n"
            + f"PROFIL_JOUR_MALADE: {c('PROFIL_JOUR_MALADE')}\n"
            + "Conserve un fait tag habitude distinct pour les jours malade (X) et pour les jours de travail.")


def lire(texte: str, maintenant: datetime) -> list[dict]:
    auj, res = maintenant.strftime("%Y-%m-%d"), []
    for x in (texte or "").split("\n"):
        x = x.strip()
        if re.match(r"^[1-5]\s*\|\s*[a-z_]+\s*\|\s*\d+\s*\|\s*\d{4}-\d{2}-\d{2}\s*\|.{5,}", x):
            p = x.split("|")
            res.append({"imp": int(p[0].strip()), "tag": p[1].strip(), "n": int(p[2].strip()),
                        "dt": p[3].strip(), "fait": "|".join(p[4:]).strip()[:120]})
        elif re.match(r"^[1-5]\s*\|\s*[a-z_]+\s*\|.{5,}", x):
            p = x.split("|")
            res.append({"imp": int(p[0].strip()), "tag": p[1].strip(), "n": 1, "dt": auj,
                        "fait": "|".join(p[2:]).strip()[:120]})
    return res


def garde_fou(lignes: list[dict], n: int) -> bool:
    """True = la consolidation serait ANNULÉE (comme le script HA)."""
    return len(lignes) < 5 or (n > 0 and len(lignes) < n * 0.5)


INTERDITS = ["purge quotidienne", "convalescent", "nuit douce", "nuit economique", "nuit économique",
             "charge 21h30", "rendez-vous medica", "rendez-vous médica", "la ruche", "auto_jam", "ade"]


def interdits_recrees(lignes: list[dict]) -> list[str]:
    """Contrôle en plus du script HA : faits qui touchent la liste INTERDITS du 13/10."""
    out = []
    for l in lignes:
        f = l["fait"].lower()
        if any(re.search(rf"\b{re.escape(m)}\b", f) for m in INTERDITS) or CF.sujet_interdit(l["fait"]):
            out.append(l["fait"][:80])
    return out


class AgentConsolidation:
    """`core` fournit : appel(), etat(), routeur, memoire."""

    def __init__(self, core):
        self.core = core
        try:
            self.dernier: dict = json.loads(core.memoire.meta("derniere_consolidation") or "{}")
        except Exception:
            self.dernier = {}

    async def _items(self) -> list[dict]:
        rep = await self.core.appel("POST", "/services/todo/get_items?return_response",
                                    {"entity_id": "todo.adria_memoire", "status": "needs_action"})
        return liste_items(rep, "todo.adria_memoire")

    async def _courtes_changees(self, maintenant: datetime) -> int:
        n = 0
        for e in COURTES:
            try:
                lc = (await self.core.appel("GET", f"/states/{e}")).get("last_changed") or ""
                if datetime.fromisoformat(lc).astimezone().replace(tzinfo=None) > maintenant - timedelta(hours=24):
                    n += 1
            except Exception:
                pass
        return n

    async def _todo(self, service: str, data: dict) -> bool:
        try:
            await self.core.appel("POST", f"/services/todo/{service}", {"entity_id": LISTE, **data})
            return True
        except Exception:
            return False

    async def _termines_copies(self) -> list[str]:
        """Comme todo.remove_completed_items du script HA, mais seulement les lignes copiées dans le Core.
        Lecture seule : renvoie les uid à retirer (l'écriture est faite dans _appliquer)."""
        rep = await self.core.appel("POST", "/services/todo/get_items?return_response",
                                    {"entity_id": LISTE, "status": "completed"})
        return [i["uid"] for i in liste_items(rep, LISTE)
                if i.get("uid") and self.core.memoire.a_la_copie("adria_memoire", i)]

    async def _interrupteurs(self) -> tuple[bool, bool]:
        """(maître ON, réel ON). Lecture HA impossible (None) = OFF : le garde-fou se ferme."""
        maitre = await self.core.etat(MAITRE) == "on"
        reel = await self.core.etat(REEL) == "on"
        return maitre, reel

    async def _appliquer(self, plan: dict) -> dict:
        """Écrit le plan dans HA, dans un ordre qui évite toute perte :
        1. ajouter les nouveaux faits (en cas d'échec : on retire ceux déjà ajoutés = compensation,
           et on s'arrête avant de toucher aux anciens) ;
        2. passer les anciens faits en « terminé » et appliquer le tri (jamais d'effacement ici) ;
        3. retirer les terminés déjà copiés dans la base du Core.
        """
        ecrit = {"ajoutes": 0, "termines": 0, "tri": 0, "retires_termines": 0, "erreurs": []}
        ajoutes: list[str] = []
        for l in plan["lignes"]:
            texte = f"[{l['tag']}] {l['fait']}"
            if await self._todo("add_item", {"item": texte, "description": l["description"]}):
                ajoutes.append(texte)
                continue
            ecrit["erreurs"].append(f"ajout impossible : {texte[:60]}")
            for t in ajoutes:  # compensation : la liste revient à son état d'avant
                await self._todo("remove_item", {"item": t})
            ecrit.update(ajoutes=0, compense=len(ajoutes))
            return ecrit
        ecrit["ajoutes"] = len(ajoutes)
        for uid in plan["terminer"]:
            if await self._todo("update_item", {"item": uid, "status": "completed"}):
                ecrit["termines"] += 1
            else:
                ecrit["erreurs"].append(f"terminer {uid}")
        for p in plan["promouvoir"]:
            if await self._todo("update_item", {"item": p["uid"], "rename": p["s"], "description": p["d"]}):
                ecrit["tri"] += 1
            else:
                ecrit["erreurs"].append(f"promouvoir {p['uid']}")
        for k in range(0, len(plan["retirer"]), 50):
            if await self._todo("remove_item", {"item": plan["retirer"][k:k + 50]}):
                ecrit["retires_termines"] += len(plan["retirer"][k:k + 50])
            else:
                ecrit["erreurs"].append("retrait des terminés")
        return ecrit

    async def consolider(self, forcer: bool = False) -> dict:
        """Essai par défaut ; réel si input_boolean.adria_core_consolidation_reelle est ON.

        v0.21.2 (fiche R2) :
        - `forcer` ne contourne PLUS l'interrupteur maître (il est seulement noté dans le résultat) ;
        - tout est PRÉPARÉ sans rien écrire dans HA (tri, IA, garde-fou, interdits) ;
        - les deux interrupteurs sont relus juste avant l'écriture ;
        - l'écriture suit _appliquer (ajouts d'abord, compensation en cas d'échec).
        """
        maintenant = datetime.now()
        maitre, reel = await self._interrupteurs()
        if not maitre:
            res = {"ok": False, "essai": True, "quand": maintenant.isoformat(timespec="seconds"),
                   "raison": f"{MAITRE.split('.')[1]} OFF ou illisible", "forcer_ignore": bool(forcer)}
            LOG.warning("Consolidation bloquée : %s", res["raison"])
            return res
        items = await self._items()
        if reel:
            # Règle de Mael : rien ne quitte HA sans copie dans le Core -> copie d'abord (base du Core,
            # jamais effacée ; ce n'est pas une écriture dans HA).
            self.core.memoire.ajouter("adria_memoire", items, maintenant)
        tri = trier(items, maintenant)
        restants = appliquer_tri(items, tri)
        res = {"essai": not reel, "quand": maintenant.isoformat(timespec="seconds"), "avant": len(items),
               "tri": {"res_vieux": len(tri["res_vieux"]), "obs_oubliees": len(tri["obs_vieilles"]),
                       "promues_regle": len(tri["promouvoir"])}}
        if forcer:
            res["forcer_ignore"] = True
        plan = {"lignes": [], "terminer": tri["obs_vieilles"] + tri["res_vieux"],
                "promouvoir": tri["promouvoir"], "retirer": []}
        if rien_de_nouveau(restants, await self._courtes_changees(maintenant)):
            res.update(ok=True, raison="rien de nouveau (0 token)")
        else:
            ctx = {k: (await self.core.etat(e)) or "" for k, e in CONTEXTE.items()}
            prompt = construire_prompt(restants, ctx, maintenant)
            rep = await self.core.routeur.demander(prompt, "rapide", "adria_core_consolidation")
            lignes = lire(rep.get("data", ""), maintenant) if rep.get("ok") else []
            annule = garde_fou(lignes, len(restants))
            res.update(ok=bool(rep.get("ok")) and not annule, moteur=rep.get("moteur"),
                       prompt_car=len(prompt), apres=len(lignes), garde_fou=annule,
                       interdits=interdits_recrees(lignes),
                       par_tag={t: sum(1 for l in lignes if l["tag"] == t) for t in sorted({l["tag"] for l in lignes})},
                       proposition=[f"{l['imp']}|{l['tag']}|{l['n']}|{l['dt']}|{l['fait']}" for l in lignes])
            if not rep.get("ok"):
                res["raison"] = str(rep.get("erreur") or "IA sans réponse")[:120]
            elif annule:
                res["raison"] = f"garde-fou : {len(lignes)} faits pour {len(restants)}"
            elif res["interdits"]:  # vérifié AVANT toute écriture, en essai comme en réel
                res.update(ok=False, raison="faits interdits recréés : rien écrit")
            plan["lignes"] = [{**l, "description": f"imp={l['imp']};dt={l['dt'] or maintenant.strftime('%Y-%m-%d')};"
                                                   f"n={l['n']};src=consolide"} for l in lignes]
            plan["terminer"] = plan["terminer"] + [i["uid"] for i in restants if i.get("uid")]
        if reel and res["ok"]:
            try:
                plan["retirer"] = await self._termines_copies()
            except Exception as e:
                res.update(ok=False, raison=f"lecture des terminés impossible : {str(e)[:80]}")
        if reel and res["ok"]:
            # Dernière vérification juste avant d'écrire : les deux interrupteurs doivent être ON.
            maitre, reel_encore = await self._interrupteurs()
            if not (maitre and reel_encore):
                res.update(ok=False, raison="interrupteur coupé pendant la préparation : rien écrit")
            else:
                LOG.info("Consolidation réelle : %s ajouts, %s terminés, %s retraits", len(plan["lignes"]),
                         len(plan["terminer"]), len(plan["retirer"]))
                ecrit = await self._appliquer(plan)
                res["ecrit"] = ecrit
                if ecrit.get("compense") is not None:
                    res.update(ok=False, raison="écriture interrompue : ajouts annulés (compensation)")
                elif ecrit["erreurs"]:
                    res.update(ok=False, raison="écriture partielle : " + "; ".join(ecrit["erreurs"])[:120])
        elif reel:
            res["ecrit"] = {}  # rien écrit dans HA
        self.dernier = res
        try:
            self.core.memoire.meta("derniere_consolidation", json.dumps(res, ensure_ascii=False))
        except Exception:
            pass
        return res

    def resume(self) -> dict | None:
        """Pour l'attribut du capteur (sans la proposition complète, trop longue)."""
        if not self.dernier:
            return None
        return {k: v for k, v in self.dernier.items() if k != "proposition"}
