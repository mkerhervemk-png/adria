"""Routeur IA d'ADRIA (sans IA lui-même) : choisit le moteur, borne les délais.

Chaîne identique au routeur YAML du 11/11 :
- interactif (voix) : Gemini 12 s -> Gemini Lite 8 s -> [Claude 15 s] -> repli
  (Ollama retiré de la voix le 24/11, R3 : 0 réponse dans les 18 s en 7 jours)
- rapide (défaut)   : Gemini 60 s -> nouvel essai 45 s plus tard -> [Claude] -> Gemini Lite 20 s -> Ollama 90 s
- profond           : [Claude] -> Gemini 60 s -> Gemini Lite 20 s -> Ollama 90 s
[Claude] seulement si input_boolean.adria_claude_actif est ON.

Coupe-circuit : un moteur qui échoue 3 fois en 10 min est sauté 15 min.
Surcharge Gemini (R3, 24/11) : une erreur 500/503 rapide est réessayée une fois après 2 s ;
3 erreurs 500/503 en 30 min -> Gemini Lite passe devant Gemini pendant 30 min
(voix et rapide ; le mode profond garde son ordre).
Gemini, Gemini Lite et Claude passent par Home Assistant (ai_task) ; Ollama est
appelé en direct, ce qui permet de l'arrêter vraiment au délai.
"""

import asyncio
import re
import time
from collections import defaultdict, deque

REPLI = "Mes IA ne répondent pas assez vite pour l'instant. Réessaie dans quelques minutes."
ERREUR = re.compile(r"^(Sorry, I had a problem|Error)")
RAISONNEMENT = re.compile(r"^(Okay|OK,|Ok,|Alright|Let me|Let.s|First|Hmm|So,|Well,|I need|The user)", re.I)
RAISONNEMENT_2 = re.compile(r"(the user|let me|I need to)", re.I)
ENTITES = {
    "gemini": "ai_task.google_ai_task_5",
    "gemini_lite": "ai_task.google_ai_task",
    "claude": "ai_task.claude_ai_task",
}
CHAINES = {
    "interactif": [("gemini", 12), ("gemini_lite", 8), ("claude", 15)],
    "rapide": [("gemini", 60), ("pause", 45), ("gemini", 60), ("claude", 60),
               ("gemini_lite", 20), ("ollama", 90)],
    "profond": [("claude", 60), ("gemini", 60), ("gemini_lite", 20), ("ollama", 90)],
}
CONSIGNE_OLLAMA = {
    "interactif": "\n\n[Mode secours local : reponds directement en francais, phrases courtes, "
                  "aucune introduction, aucun raisonnement.] /no_think",
    "autre": "\n\n[Mode secours local : reponds directement en francais, respecte EXACTEMENT le "
             "format et le nombre de lignes demandes, aucune phrase d'introduction, aucun "
             "raisonnement.] /no_think",
}


SURCHARGE = re.compile(r"\b50[03]\b")


def nettoyer_ollama(texte: str) -> str:
    return re.sub(r"(?s)<think>.*?</think>", "", texte or "").strip()


def reponse_valide(texte: str, moteur: str) -> bool:
    t = (texte or "").strip()
    if not t or ERREUR.search(t):
        return False
    if moteur == "ollama" and (RAISONNEMENT.search(t) or RAISONNEMENT_2.search(t)):
        return False
    return True


class CoupeCircuit:
    def __init__(self, echecs: int = 3, fenetre: float = 600, pause: float = 900):
        self.echecs, self.fenetre, self.pause = echecs, fenetre, pause
        self.historique: dict[str, deque] = defaultdict(deque)
        self.ouvert_jusqu_a: dict[str, float] = {}

    def ouvert(self, moteur: str, maintenant: float | None = None) -> bool:
        return (time.time() if maintenant is None else maintenant) < self.ouvert_jusqu_a.get(moteur, 0)

    def echec(self, moteur: str, maintenant: float | None = None) -> bool:
        """Note un échec ; renvoie True si le moteur vient d'être coupé."""
        t = time.time() if maintenant is None else maintenant
        h = self.historique[moteur]
        h.append(t)
        while h and t - h[0] > self.fenetre:
            h.popleft()
        if len(h) >= self.echecs:
            self.ouvert_jusqu_a[moteur] = t + self.pause
            h.clear()
            return True
        return False

    def succes(self, moteur: str) -> None:
        self.historique[moteur].clear()

    def etat(self, maintenant: float | None = None) -> dict[str, int]:
        t = time.time() if maintenant is None else maintenant
        return {m: int(f - t) for m, f in self.ouvert_jusqu_a.items() if f > t}


class Surcharge:
    """Gemini saturé côté Google : 3 erreurs 500/503 en 30 min -> Lite devant pendant 30 min."""

    def __init__(self, erreurs: int = 3, fenetre: float = 1800, duree: float = 1800):
        self.erreurs, self.fenetre, self.duree = erreurs, fenetre, duree
        self.historique: deque = deque()
        self.jusqu_a = 0.0

    def noter(self, maintenant: float | None = None) -> bool:
        t = time.time() if maintenant is None else maintenant
        self.historique.append(t)
        while self.historique and t - self.historique[0] > self.fenetre:
            self.historique.popleft()
        if len(self.historique) >= self.erreurs and t >= self.jusqu_a:
            self.jusqu_a = t + self.duree
            self.historique.clear()
            return True
        return False

    def active(self, maintenant: float | None = None) -> bool:
        return (time.time() if maintenant is None else maintenant) < self.jusqu_a

    def reste(self, maintenant: float | None = None) -> int:
        t = time.time() if maintenant is None else maintenant
        return max(0, int(self.jusqu_a - t))


def ordonner(chaine: list, surcharge: bool, prof: str) -> list:
    """En surcharge, Gemini Lite passe en tête (sauf mode profond)."""
    if not surcharge or prof == "profond" or not any(m == "gemini_lite" for m, _ in chaine):
        return chaine
    lite = [e for e in chaine if e[0] == "gemini_lite"][:1]
    return lite + [e for e in chaine if e[0] != "gemini_lite"]


class Routeur:
    """`ha` doit fournir : etat(entity_id) -> str|None, ai_task(entite, prompt, tache) -> str,
    ollama(prompt) -> str, statut(texte), incrementer(counter), noter(moteur, prof, ok, duree, raison)."""

    PAUSE_REESSAI = 2.0  # secondes avant le nouvel essai sur erreur 500/503 rapide

    def __init__(self, ha, coupe_circuit: CoupeCircuit | None = None,
                 surcharge: Surcharge | None = None):
        self.ha = ha
        self.cc = coupe_circuit or CoupeCircuit()
        self.surcharge = surcharge or Surcharge()

    async def autorise(self, moteur: str, force_gemini: bool) -> str | None:
        """Renvoie la raison de sauter le moteur, ou None s'il peut être utilisé."""
        if self.cc.ouvert(moteur):
            return "coupe-circuit"
        if moteur == "gemini":
            n = int(float(await self.ha.etat("counter.avatar_gemini_calls_today") or 0))
            ko = await self.ha.etat("input_boolean.ia_gemini_ko") == "on"
            if not force_gemini and (n >= 100 or ko):
                return "quota ou Gemini KO"
        if moteur == "claude" and await self.ha.etat("input_boolean.adria_claude_actif") != "on":
            return "Claude coupé"
        if moteur == "ollama" and await self.ha.etat("input_boolean.adria_ollama_secours_actif") != "on":
            return "Ollama coupé"
        return None

    async def appeler(self, moteur: str, prompt: str, tache: str) -> str:
        if moteur == "ollama":
            return nettoyer_ollama(await self.ha.ollama(prompt))
        return await self.ha.ai_task(ENTITES[moteur], prompt, f"{tache}_{moteur}")

    @staticmethod
    def erreur_surcharge(raison: str | None) -> bool:
        return bool(raison and raison.startswith("erreur") and SURCHARGE.search(raison))

    async def essayer(self, moteur: str, prompt: str, tache: str, delai: float):
        try:
            rep = await asyncio.wait_for(self.appeler(moteur, prompt, tache), delai)
            ok = reponse_valide(rep, moteur)
            return rep, ok, (None if ok else "réponse vide ou invalide")
        except asyncio.TimeoutError:
            return "", False, f"trop lent (> {delai} s)"
        except Exception as e:  # erreur réseau, service absent…
            return "", False, f"erreur : {str(e)[:80]}"

    async def demander(self, prompt: str, profondeur: str = "rapide", tache: str = "adria",
                       prompt_secours: str = "", force_gemini: bool = False,
                       seulement: str = "") -> dict:
        prof = profondeur if profondeur in CHAINES else "rapide"
        chaine = CHAINES[prof]
        if seulement:  # mode test : un seul moteur, délai le plus long prévu pour lui
            delais = [d for m, d in sum(CHAINES.values(), []) if m == seulement]
            chaine = [(seulement, max(delais))] if delais else []
        else:
            chaine = ordonner(chaine, self.surcharge.active(), prof)
        essais = []
        for moteur, delai in chaine:
            if moteur == "pause":  # nouvel essai Gemini seulement s'il a vraiment été appelé
                if essais and essais[-1][0] == "gemini" and essais[-1][2]:
                    await asyncio.sleep(delai)
                continue
            raison = await self.autorise(moteur, force_gemini)
            if raison:
                essais.append((moteur, raison, False))
                continue
            texte_prompt = prompt
            if moteur == "ollama":
                base = prompt_secours.strip() or prompt
                texte_prompt = base[:3000] + CONSIGNE_OLLAMA["interactif" if prof == "interactif" else "autre"]
            debut = time.monotonic()
            rep, ok, raison = await self.essayer(moteur, texte_prompt, tache, delai)
            if (not ok and moteur.startswith("gemini") and self.erreur_surcharge(raison)
                    and time.monotonic() - debut < 3 and not seulement):
                await asyncio.sleep(self.PAUSE_REESSAI)  # Google saturé : un seul nouvel essai
                rep, ok, raison = await self.essayer(moteur, texte_prompt, tache, delai)
            if not ok and moteur == "gemini" and self.erreur_surcharge(raison):
                if self.surcharge.noter():
                    raison += " -> Lite devant 30 min"
            duree = round(time.monotonic() - debut, 1)
            await self.ha.noter(moteur, prof, ok, duree, raison)
            if ok:
                self.cc.succes(moteur)
                if moteur == "gemini":
                    await self.ha.incrementer("counter.avatar_gemini_calls_today")
                if moteur == "claude":
                    await self.ha.incrementer("counter.adria_claude_calls_today")
                await self.ha.statut(f"{moteur} OK ({prof}, {duree} s) [Core]")
                return {"ok": True, "data": rep.strip(), "moteur": moteur, "duree": duree}
            if moteur == "gemini" and prof == "interactif" and raison and raison.startswith("trop lent"):
                await self.ha.incrementer("counter.adria_gemini_lent_semaine")
            if self.cc.echec(moteur):
                raison += " -> coupé 15 min"
            essais.append((moteur, raison, True))
        resume = ", ".join(f"{m} : {r}" for m, r, _ in essais)
        await self.ha.statut(f"CIRCUIT OUVERT {prof} [Core] — {resume}")
        return {"ok": False, "data": REPLI if prof == "interactif" else "", "moteur": None,
                "duree": None, "essais": resume}
