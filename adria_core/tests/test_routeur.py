import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from routeur import REPLI, CoupeCircuit, Routeur, nettoyer_ollama, reponse_valide  # noqa: E402


class FauxHA:
    def __init__(self, reponses, etats=None, lenteurs=None):
        self.reponses = reponses          # moteur -> texte ou Exception
        self.lenteurs = lenteurs or {}    # moteur -> secondes
        self.etats = {"counter.avatar_gemini_calls_today": "3", "input_boolean.ia_gemini_ko": "off",
                      "input_boolean.adria_claude_actif": "off",
                      "input_boolean.adria_ollama_secours_actif": "on", **(etats or {})}
        self.appels, self.notes, self.compteurs, self.statuts = [], [], [], []

    async def etat(self, e):
        return self.etats.get(e)

    async def _rep(self, moteur, prompt):
        self.appels.append((moteur, prompt))
        await asyncio.sleep(self.lenteurs.get(moteur, 0))
        r = self.reponses.get(moteur, "")
        if isinstance(r, Exception):
            raise r
        return r

    async def ai_task(self, entite, prompt, tache):
        moteur = {"ai_task.google_ai_task_5": "gemini", "ai_task.google_ai_task": "gemini_lite",
                  "ai_task.claude_ai_task": "claude"}[entite]
        return await self._rep(moteur, prompt)

    async def ollama(self, prompt):
        return await self._rep("ollama", prompt)

    async def statut(self, t):
        self.statuts.append(t)

    async def incrementer(self, c):
        self.compteurs.append(c)

    async def noter(self, *a):
        self.notes.append(a)


def lancer(coro):
    return asyncio.run(coro)


def test_gemini_ok_et_compteur():
    ha = FauxHA({"gemini": "Bonjour"})
    r = lancer(Routeur(ha).demander("Q", "interactif"))
    assert r["ok"] and r["moteur"] == "gemini" and r["data"] == "Bonjour"
    assert ha.compteurs == ["counter.avatar_gemini_calls_today"]


def test_voix_gemini_lent_bascule_lite(monkeypatch):
    import routeur
    monkeypatch.setitem(routeur.CHAINES, "interactif", [("gemini", 0.05), ("gemini_lite", 0.5),
                                                         ("claude", 0.5), ("ollama", 0.5)])
    ha = FauxHA({"gemini": "trop tard", "gemini_lite": "Lite"}, lenteurs={"gemini": 0.3})
    r = lancer(Routeur(ha).demander("Q", "interactif"))
    assert r["moteur"] == "gemini_lite"
    assert "counter.adria_gemini_lent_semaine" in ha.compteurs


def test_claude_coupe_saute_et_ollama_prompt_secours():
    ha = FauxHA({"gemini": "", "gemini_lite": "Error 503", "ollama": "<think>x</think>Réponse locale"})
    r = lancer(Routeur(ha).demander("LONG PROMPT", "interactif", prompt_secours="COURT"))
    assert r["moteur"] == "ollama" and r["data"] == "Réponse locale"
    assert "claude" not in [a[0] for a in ha.appels]
    assert ha.appels[-1][1].startswith("COURT") and "/no_think" in ha.appels[-1][1]


def test_tout_ko_voix_donne_le_repli():
    ha = FauxHA({"gemini": RuntimeError("503"), "gemini_lite": "", "ollama": "Okay, let me think"})
    r = lancer(Routeur(ha).demander("Q", "interactif"))
    assert not r["ok"] and r["data"] == REPLI
    assert "CIRCUIT OUVERT" in ha.statuts[-1]


def test_quota_gemini_sans_pause(monkeypatch):
    import routeur
    monkeypatch.setitem(routeur.CHAINES, "rapide", [("gemini", 1), ("pause", 5), ("gemini", 1),
                                                     ("gemini_lite", 1)])
    ha = FauxHA({"gemini_lite": "Lite"}, etats={"counter.avatar_gemini_calls_today": "100"})
    import time
    t = time.monotonic()
    r = lancer(Routeur(ha).demander("Q"))
    assert r["moteur"] == "gemini_lite" and time.monotonic() - t < 1  # pas de pause de 5 s


def test_coupe_circuit():
    cc = CoupeCircuit()
    assert not cc.echec("gemini", 100) and not cc.echec("gemini", 200)
    assert cc.echec("gemini", 300) and cc.ouvert("gemini", 400) and not cc.ouvert("gemini", 1300)
    cc2 = CoupeCircuit()
    cc2.echec("gemini", 0)
    cc2.echec("gemini", 100)
    assert not cc2.echec("gemini", 800)  # hors de la fenêtre de 10 min


def test_coupe_circuit_saute_le_moteur():
    ha = FauxHA({"gemini": RuntimeError("503"), "gemini_lite": "Lite"})
    rt = Routeur(ha)
    for _ in range(3):
        lancer(rt.demander("Q", "interactif"))
    n = len([a for a in ha.appels if a[0] == "gemini"])
    lancer(rt.demander("Q", "interactif"))
    assert len([a for a in ha.appels if a[0] == "gemini"]) == n  # Gemini sauté
    assert "gemini" in rt.cc.etat()


def test_validation():
    assert not reponse_valide("Okay, so", "ollama") and reponse_valide("Okay, so", "gemini")
    assert nettoyer_ollama("<think>a\nb</think> ok ") == "ok"


def test_stats_ia():
    from store import Memoire
    m = Memoire(":memory:")
    m.noter_ia("gemini", "interactif", True, 2.0, None, quand=1000)
    m.noter_ia("gemini", "interactif", False, 12.0, "trop lent", quand=1000)
    m.noter_ia("ollama", "rapide", True, 30.0, None, quand=10)
    s = m.stats_ia(500, maintenant=1100)
    assert s == {"gemini": {"appels": 2, "ok": 1, "moy_s": 2.0}}


def test_mode_test_un_seul_moteur():
    ha = FauxHA({"gemini": "G", "ollama": "Réponse locale"})
    r = lancer(Routeur(ha).demander("Q", "interactif", seulement="ollama"))
    assert r["moteur"] == "ollama" and [a[0] for a in ha.appels] == ["ollama"]


def test_echecs_par_raison():
    import os, tempfile, sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
    from store import Memoire
    m = Memoire(os.path.join(tempfile.mkdtemp(), "t.db"))
    m.noter_ia("gemini", "interactif", False, 12.0, "trop lent (> 12 s)", quand=1000)
    m.noter_ia("gemini", "interactif", False, 12.0, "trop lent (> 12 s) -> coupé 15 min", quand=1001)
    m.noter_ia("ollama", "rapide", False, 1.0, "erreur : connexion refusée sur le port 11434 très long texte", quand=1002)
    m.noter_ia("gemini", "rapide", True, 3.0, None, quand=1003)
    e = m.stats_ia_echecs(10_000, maintenant=2000)
    assert e["gemini"] == {"trop lent (> 12 s)": 2}
    assert list(e["ollama"].values()) == [1] and len(list(e["ollama"])[0]) <= 40
