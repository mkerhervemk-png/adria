"""Mémoire procédurale (lot PR1, 19/11/2026) : « comment on fait » — les savoir-faire d'ADRIA.

Une procédure = un nom + des étapes. Elles viennent de :
- Mael à la voix : « ADRIA, retiens comment faire X : étape 1, puis étape 2, ensuite étape 3 » (0 IA) ;
- les compétences apprises acceptées (lot C1) et les rappels d'habitude acceptés (lot H1), repris
  automatiquement chaque nuit ;
- Claude (événement `adria_core_procedure` mode « retenir »).
On la redemande par « comment on fait X ? » / « rappelle-moi comment X ».
Rien n'est effacé : une procédure retirée passe seulement à actif = 0. Copie dans la mémoire
(source « procedures ») pour la recherche par mots et par le sens.
Interrupteur : input_boolean.adria_core_procedures.
"""

import json
import re
from datetime import datetime

from store import mots_cles, nb_mots, normaliser

INTERRUPTEUR = "input_boolean.adria_core_procedures"
CAPTEUR = "sensor.adria_core_procedures"
DEBUT = re.compile(r"^.*?\b(?:retiens|retenir|apprends|m[ée]morise|note)\s+(?:bien\s+)?comment\s+"
                   r"(?:on\s+)?(?:fait\s+|faire\s+|faut\s+faire\s+)?", re.I)
MOTS_LIEN = r"(?:puis|ensuite|apr[eè]s\s+[cç]a|et\s+enfin|enfin)\s+"
SEPARATEURS = re.compile(r"(?:\s*[,;\n]\s*(?:" + MOTS_LIEN + r")?|\s+" + MOTS_LIEN + ")", re.I)


def depuis_texte(texte: str) -> tuple[str, list[str]] | None:
    """« retiens comment faire X : a, puis b » -> ("X", ["a", "b"]). Sans « : », pas de procédure."""
    t = (texte or "").strip()
    if not DEBUT.search(t) or ":" not in t:
        return None
    nom, _, reste = DEBUT.sub("", t, count=1).partition(":")
    nom = nom.strip(" .")
    etapes = [e.strip(" .") for e in SEPARATEURS.split(reste) if e and len(e.strip(" .")) > 1]
    if len(nom) < 3 or not etapes:
        return None
    return nom[:120], [e[:200] for e in etapes[:15]]


class Procedures:
    def __init__(self, memoire):
        self.m = memoire
        self.m.db.execute(
            "CREATE TABLE IF NOT EXISTS procedures (id INTEGER PRIMARY KEY, cle TEXT UNIQUE, nom TEXT NOT NULL,"
            " etapes TEXT NOT NULL, source TEXT, creee TEXT NOT NULL, maj TEXT NOT NULL,"
            " utilisations INTEGER NOT NULL DEFAULT 0, actif INTEGER NOT NULL DEFAULT 1)")
        self.m.db.commit()

    def retenir(self, nom: str, etapes: list[str], source: str, cle: str | None = None,
                quand: datetime | None = None) -> tuple[int, bool]:
        """Ajoute ou met à jour (même clé). -> (id, nouvelle ?)"""
        q = (quand or datetime.now()).isoformat(timespec="seconds")
        cle = cle or "p_" + re.sub(r"[^a-z0-9]+", "_", normaliser(nom)).strip("_")[:60]
        r = self.m.db.execute("SELECT id, etapes FROM procedures WHERE cle = ?", (cle,)).fetchone()
        txt = json.dumps(etapes, ensure_ascii=False)
        if r:
            if r[1] != txt:
                self.m.db.execute("UPDATE procedures SET nom = ?, etapes = ?, maj = ?, actif = 1 WHERE id = ?",
                                  (nom, txt, q, r[0]))
                self.m.db.commit()
                self._copier(nom, etapes, q)
            return r[0], False
        cur = self.m.db.execute(
            "INSERT INTO procedures (cle, nom, etapes, source, creee, maj) VALUES (?, ?, ?, ?, ?, ?)",
            (cle, nom, txt, source, q, q))
        self.m.db.commit()
        self._copier(nom, etapes, q)
        return cur.lastrowid, True

    def _copier(self, nom: str, etapes: list[str], q: str) -> None:
        self.m.ajouter("procedures", [{"summary": f"Comment {nom} : " + " ; ".join(etapes), "date": q[:10]}])

    def retirer(self, ident: int) -> bool:
        cur = self.m.db.execute("UPDATE procedures SET actif = 0 WHERE id = ?", (ident,))
        self.m.db.commit()
        return bool(cur.rowcount)

    def toutes(self, actives: bool = True) -> list[dict]:
        rows = self.m.db.execute("SELECT * FROM procedures" + (" WHERE actif = 1" if actives else "")
                                 + " ORDER BY maj DESC").fetchall()
        return [{**dict(r), "etapes": json.loads(r["etapes"])} for r in rows]

    def chercher(self, question: str, n: int = 3) -> list[dict]:
        mots = [m for m in mots_cles(question) if m not in ("comme", "retie", "proce", "facon", "etape")]
        if not mots:
            return []
        res = []
        for p in self.toutes():
            score = nb_mots(p["nom"], mots) * 2 + nb_mots(" ".join(p["etapes"]), mots)
            if nb_mots(p["nom"] + " " + " ".join(p["etapes"]), mots) >= max(1, (len(mots) + 1) // 2):
                res.append((score, p))
        res.sort(key=lambda x: (-x[0], x[1]["maj"]), reverse=False)
        return [p for _, p in res[:n]]

    def utilisee(self, ident: int) -> None:
        self.m.db.execute("UPDATE procedures SET utilisations = utilisations + 1 WHERE id = ?", (ident,))
        self.m.db.commit()

    # -------------------------------------------------- reprise des compétences et habitudes acceptées
    def reprendre(self, competences_creees: list[dict], lignes_competences: list[str],
                  rappels_habitudes: list[dict], regles: dict) -> int:
        n = 0
        for c in competences_creees:
            explic = next((l for l in lignes_competences if c.get("alias", "")[:20].lower() in l.lower()), "")
            etapes = [f"automatisation « {c['alias']} » (étiquette agent_appris)"]
            if explic:
                etapes.insert(0, explic.removeprefix("Compétence apprise : "))
            n += self.retenir(c["alias"], etapes, "competence", "comp_" + str(c["id"]))[1]
        for r in rappels_habitudes:
            if not r.get("actif") or r.get("regle") not in regles:
                continue
            g = regles[r["regle"]]
            n += self.retenir(f"rappel d'habitude {g['titre']} ({r.get('cle')})",
                              [f"constat : {g['constat']}", f"rappel : {g['rappel']}",
                               "arrêt : bouton « Arrêter ce rappel » sur la notification"],
                              "habitude", "hab_" + str(r.get("id")))[1]
        return n

    def repondre(self, data: dict) -> dict:
        """Voix : mode « retenir » (texte complet) ou « demander » (question)."""
        res = {"req_id": data.get("req_id", ""), "ok": False, "texte": ""}
        texte = str(data.get("texte") or data.get("question") or "")
        if str(data.get("mode") or "demander") == "retenir":
            if str(data.get("qui") or "Mael").lower() not in ("mael", "claude"):
                res["texte"] = "Seul Mael peut m'apprendre une procédure."
                return res
            p = depuis_texte(texte)
            if not p:
                res["texte"] = ("Dis-le comme ça : retiens comment faire telle chose, deux points, "
                                "étape un, puis étape deux.")
                return res
            _, nouvelle = self.retenir(p[0], p[1], str(data.get("qui") or "mael").lower())
            res.update(ok=True, texte=f"{'Retenu' if nouvelle else 'Mis à jour'} : {p[0]}, en {len(p[1])} étape"
                                      f"{'s' if len(p[1]) > 1 else ''}.")
            return res
        trouves = self.chercher(texte)
        if not trouves:
            res["texte"] = "Je n'ai pas de procédure pour ça."
            return res
        p = trouves[0]
        self.utilisee(p["id"])
        etapes = " ; ".join(f"{i}. {e}" for i, e in enumerate(p["etapes"], 1))
        res.update(ok=True, texte=f"Pour {p['nom']} : {etapes}."[:900], procedure=p["nom"])
        return res

    def etat(self) -> dict:
        ps = self.toutes()
        return {"state": len(ps), "attributes": {
            "friendly_name": "ADRIA Core — procédures", "icon": "mdi:format-list-checks",
            "unit_of_measurement": "procédures",
            "liste": [f"{p['nom']} ({len(p['etapes'])} ét., {p['source']}, utilisée {p['utilisations']}×)"
                      for p in ps[:15]]}}
