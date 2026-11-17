"""Routeur IA d'ADRIA (sans IA lui-même) : choisit le moteur, borne les délais.

Chaîne identique au routeur YAML du 11/11 :
- interactif (voix) : Gemini 12 s -> Gemini Lite 8 s -> [Claude 15 s] -> Ollama 18 s -> repli
- rapide (défaut)   : Gemini 60 s -> nouvel essai 45 s plus tard -> [Claude] -> Gemini Lite 20 s -> Ollama 90 s
- profond           : [Claude] -> Gemini 60 s -> Gemini Lite 20 s -> Ollama 90 s
[Claude] seulement si input_boolean.adria_claude_actif est ON.

Coupe-circuit : un moteur qui échoue 3 fois en 10 min est sauté 15 min.
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
    "interactif": [("gemini", 12), ("gemini_lite", 8), ("claude", 15), ("ollama", 18)],
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


class Routeur:
    """`ha` doit fournir : etat(entity_id) -> str|None, ai_task(entite, prompt, tache) -> str,
    ollama(prompt) -> str, statut(texte), incrementer(counter), noter(moteur, prof, ok, duree, raison)."""

    def __init__(self, ha, coupe_circuit: CoupeCircuit | None = None):
        self.ha = ha
        self.cc = coupe_circuit or CoupeCircuit()

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

    async def demander(self, prompt: str, profondeur: str = "rapide", tache: str = "adria",
                       prompt_secours: str = "", force_gemini: bool = False) -> dict:
        prof = profondeur if profondeur in CHAINES else "rapide"
        essais = []
        for moteur, delai in CHAINES[prof]:
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
            try:
                rep = await asyncio.wait_for(self.appeler(moteur, texte_prompt, tache), delai)
                ok = reponse_valide(rep, moteur)
                raison = None if ok else "réponse vide ou invalide"
            except asyncio.TimeoutError:
                rep, ok, raison = "", False, f"trop lent (> {delai} s)"
            except Exception as e:  # erreur réseau, service absent…
                rep, ok, raison = "", False, f"erreur : {str(e)[:80]}"
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
