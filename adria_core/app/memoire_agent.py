"""Agent Mémoire (lot M2a) : carnet de bord du soir + réflexion d'ADRIA, faits par le Core.

Remplace l'automatisation HA « ADRIA Jarvis P4 — Carnet de bord quotidien (23h40) » quand
input_boolean.adria_core_memoire est ON (l'automatisation HA a la condition inverse).
Même sortie qu'avant (todo.adria_jarvis_episodes, todo.adria_journal_interieur,
input_text.adria_conscience_derniere_reflexion), même appel IA unique, mais avec en plus
les heures réelles de présence tirées de l'historique du Core.
Rien n'est jamais effacé du Core.
"""

import re
from datetime import date, datetime

JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
SOURCES_PRESENCE = ("person.", "sensor.adria_ou_", "input_select.quart_mael", "input_boolean.mode_nuit")


def presences_du_jour(lignes_historique: list[dict], maxi: int = 1500) -> str:
    """Lignes « JJ/MM HH:MM Nom : état » de présence/quart/mode nuit, compactées."""
    garde = [l["texte"].split(" ", 1)[1] if " " in l["texte"] else l["texte"]
             for l in lignes_historique
             if (l.get("detail") or "").startswith(SOURCES_PRESENCE)]
    tv = sum(1 for l in lignes_historique if (l.get("detail") or "").startswith("media_player."))
    texte = " ; ".join(garde)[:maxi]
    if tv:
        texte += f" ; (TV/décodeur : {tv} changements d'état)"
    return texte or "aucune donnée"


def construire_prompt(jour: date, d: dict) -> str:
    jour_txt = f"{JOURS[jour.weekday()]} {jour.strftime('%d/%m/%Y')}"
    return (
        "Tu tiens le carnet de bord de la Maison Kerherve (foyer : Mael, Vanessa, Ryan, Iwan, Aelia). "
        "Reponds en DEUX lignes. LIGNE 1 commence par CARNET: puis resume la journee du "
        f"{jour_txt} en une ligne de 230 caracteres maximum, factuelle, en francais, style carnet de "
        "bord : qui est sorti ou rentre et quand (seulement les faits marquants, pas chaque "
        "aller-retour ; appuie-toi sur PRESENCES_REELLES pour les heures), quart ou repos de Mael, "
        "evenements de la famille, Inster, Tempo, meteo, demandes notables faites a ADRIA. Pas de "
        "sante, pas de detail de securite, pas d introduction, pas de date en tete. "
        f"JOURNAL : {d.get('evts') or 'vide'}. "
        f"PRESENCES_REELLES (historique de la maison, heure locale) : {d.get('presences') or 'aucune donnée'}. "
        f"COMMANDES_VOCALES : {d.get('nb_voix', 0)}. "
        f"DEMANDES_A_ADRIA : {d.get('demandes') or 'aucune'}. "
        f"MEMOIRE_VIE : {d.get('mem_vie')}. QUART : {d.get('quart')}. TEMPO : {d.get('tempo')}. "
        f"METEO : {d.get('meteo')}. "
        "LIGNE 2 commence par REFLEXION: puis ta reflexion du soir d ADRIA, a la premiere personne, "
        "230 caracteres maximum, au format exact : Reussi : ... | A ameliorer : ... | Demain je : ... . "
        "Demain je = UNE chose concrete et verifiable que je ferai moi-meme dans mes analyses, tiree "
        "des donnees (verifier, comparer ou surveiller une donnee precise), differente de "
        "REFLEXION_D_HIER ; jamais une promesse de notification, de rappel, de message ou d action sur "
        "un appareil, car je n en declenche pas seule (interdit aussi : formules generales comme rester "
        "attentive, optimiser le confort, accompagner la famille). Une difficulte se dit comme un defi, jamais une plainte. Pas de sante, pas "
        f"de securite. QUESTIONS_SANS_REPONSE_AUJOURD_HUI : {d.get('lacunes') or 'aucune'}. "
        f"ECHECS_VOCAUX_SEMAINE : {d.get('echecs')}. SANTE_IA : {d.get('sante')}/100. "
        f"TAUX_ACCEPTATION_PROPOSITIONS : {d.get('taux')} %. HUMEUR : {d.get('humeur')}. "
        f"REFLEXION_D_HIER : {d.get('reflexion_hier') or 'aucune'}."
    )


def lire_reponse(texte: str) -> tuple[str, str]:
    brut = re.sub(r"[*#_`]", "", texte or "")
    lignes = [l.strip() for l in brut.split("\n") if l.strip()]
    carnet = next((l for l in lignes if re.match(r"(?i)^carnet\s*:", l)), None)
    if carnet is None:
        carnet = next((l for l in lignes if not re.match(r"(?i)^r.flexion\s*:", l)), "")
    reflexion = next((l for l in lignes if re.match(r"(?i)^r.flexion\s*:", l)), "")
    carnet = re.sub(r"(?i)^carnet\s*:\s*", "", carnet).strip()
    reflexion = re.sub(r"(?i)^r.flexion\s*:\s*", "", reflexion).strip()
    return carnet, reflexion


def couper(texte: str, maxi: int = 230) -> str:
    """Coupe proprement : à la dernière phrase complète, sinon au dernier mot (avec …)."""
    texte = (texte or "").strip()
    if len(texte) <= maxi:
        return texte
    debut = texte[:maxi]
    fin = max(debut.rfind(". "), debut.rfind(" ; "), debut.rfind(" | "))
    if fin >= maxi // 2:
        return debut[:fin + 1].rstrip(" ;|")
    return debut[:maxi - 1].rsplit(" ", 1)[0].rstrip(" ,;(") + "…"


def items(rep: dict, liste: str) -> list[dict]:
    return (rep.get("service_response") or {}).get(liste, {}).get("items", []) if rep else []


class AgentMemoire:
    """`core` fournit : appel(), etat(), routeur, memoire, importer_historique()."""

    def __init__(self, core):
        self.core = core
        self.dernier: dict = {}

    async def liste(self, entite: str) -> list[dict]:
        try:
            rep = await self.core.appel("POST", "/services/todo/get_items?return_response",
                                        {"entity_id": entite, "status": "needs_action"})
            return items(rep, entite)
        except Exception:
            return []

    async def donnees(self, jour: date) -> dict:
        jj = jour.strftime("%d/%m")
        jr = [i.get("summary", "") for i in await self.liste("todo.adria_journal")]
        mp = [i.get("summary", "") for i in await self.liste("todo.adria_memoire_perso")]
        lc = await self.liste("todo.adria_lacunes")
        evts = [s for s in jr if s.startswith(jj) and "utilisee (commande vocale)" not in s]
        try:
            await self.core.importer_historique()  # historique à jour jusqu'à maintenant
        except Exception:
            pass
        hist = self.core.memoire.chercher_periode(jour.isoformat(), jour.isoformat(), [], 400, [])
        hist = [h for h in hist if h["source"] == "historique"]
        e = self.core.etat
        meteo = "weather.meteo_france_forecast_for_city_saint_brieuc_bretagne_22_fr_saint_brieuc"
        return {
            "evts": " ; ".join(evts)[:2500],
            "nb_voix": sum(1 for s in jr if s.startswith(jj) and "commande vocale" in s),
            "demandes": " ; ".join(s for s in mp if f"|{jj}]" in s)[:900],
            "lacunes": " ; ".join(i.get("summary", "")[:80] for i in lc
                                  if f"dt={jour.isoformat()}" in (i.get("description") or "")),
            "presences": presences_du_jour(hist),
            "mem_vie": await e("input_text.adria_mem_vie"),
            "quart": await e("input_select.quart_mael"),
            "tempo": await e("input_select.tempo_couleur_du_jour"),
            "meteo": await e(meteo),
            "echecs": await e("counter.adria_voix_echecs_semaine"),
            "sante": await e("sensor.ia_health_score"),
            "taux": await e("sensor.avatar_taux_acceptation"),
            "humeur": await e("sensor.adria_humeur"),
            "reflexion_hier": await e("input_text.adria_conscience_derniere_reflexion"),
        }

    async def carnet(self, essai: bool = False, forcer: bool = False) -> dict:
        """Fait le carnet du jour. essai=True : n'écrit rien dans HA (comparaison)."""
        e = self.core.etat
        if not forcer and not essai:
            if await e("input_boolean.adria_core_memoire") != "on":
                return {"ok": False, "raison": "adria_core_memoire OFF"}
            if await e("input_boolean.adria_jarvis_actif") != "on":
                return {"ok": False, "raison": "adria_jarvis_actif OFF"}
        jour = date.today()
        d = await self.donnees(jour)
        rep = await self.core.routeur.demander(construire_prompt(jour, d), "rapide", "adria_core_carnet")
        carnet, reflexion = lire_reponse(rep.get("data", "")) if rep.get("ok") else ("", "")
        carnet, reflexion = couper(carnet), couper(reflexion)
        jour_txt = f"{JOURS[jour.weekday()]} {jour.strftime('%d/%m/%Y')}"
        jour_court = f"{JOURS[jour.weekday()]} {jour.strftime('%d/%m')}"
        res = {"ok": len(carnet) > 10, "carnet": carnet[:230], "reflexion": reflexion[:230],
               "moteur": rep.get("moteur"), "presences": d["presences"][:300], "essai": essai,
               "quand": datetime.now().isoformat(timespec="seconds")}
        if not essai and res["ok"]:
            ligne = f"[{jour_txt}] {carnet[:230]}"
            await self.core.appel("POST", "/services/todo/add_item",
                                  {"entity_id": "todo.adria_jarvis_episodes", "item": ligne})
            self.core.memoire.ajouter("adria_jarvis_episodes",
                                      [{"summary": ligne, "date": jour.isoformat()}])
            if len(reflexion) > 15 and await e("input_boolean.adria_conscience_actif") == "on":
                r = f"[{jour_court}] {reflexion[:230]}"
                await self.core.appel("POST", "/services/todo/add_item",
                                      {"entity_id": "todo.adria_journal_interieur", "item": r})
                await self.core.appel("POST", "/services/input_text/set_value", {
                    "entity_id": "input_text.adria_conscience_derniere_reflexion", "value": r[:255]})
                self.core.memoire.ajouter("adria_journal_interieur",
                                          [{"summary": r, "date": jour.isoformat()}])
        self.dernier = res
        return res
