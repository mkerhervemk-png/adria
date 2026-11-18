"""Journal de famille de l'année (lot J1), 0 IA au quotidien.

- Dimanche (heure `journal_heure`, défaut 17:45) : ADRIA relit l'agenda des 7 derniers jours dans sa base
  et propose 1 à 3 « beaux moments » (patin, matchs, sorties, anniversaires…), chacun avec OUI / NON.
  Un titre refusé n'est plus reproposé pendant 60 jours (le patin de chaque samedi ne revient pas en boucle).
- À la voix : « ADRIA, garde ce souvenir : Aelia a eu sa 1re médaille » -> gardé tout de suite
  (événement `adria_core_souvenir` {texte, qui}).
- 15 décembre 10h00 (ou événement `adria_core_livre` {annee}) : le livre de l'année, mois par mois,
  en page HTML classée dans Foyer/Souvenirs (retrouvable avec « sors-moi le livre de famille »).
  Une seule phrase d'introduction écrite par l'IA (1 appel par an) ; sans IA, le livre se fait quand même.

Rien n'est gardé sans OUI (ou demande explicite), rien n'est jamais effacé. Actif si
input_boolean.adria_core_journal est ON.
"""

import html
import json
import logging
import os
import re
import uuid
from datetime import date, datetime, timedelta

from store import cle_doublon, normaliser

LOG = logging.getLogger("adria_core.journal")

INTERRUPTEUR = "input_boolean.adria_core_journal"
MAX_PROPOSITIONS = 3
REFUS_JOURS = 60
DOSSIER_LIVRE = ("Foyer", "Souvenirs")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
        "octobre", "novembre", "décembre")
# un « beau moment » parle de l'un de ces sujets…
BEAUX = re.compile(
    r"patin|gala|competition|compet|match|tournoi|arbitr|foot|badminton|pompier|jsp|protection civile|"
    r"anniversaire|fete|mariage|bapteme|communion|naissance|spectacle|concert|cinema|theatre|cirque|"
    r"restaurant|resto|sortie|balade|plage|piscine|parc|zoo|vacances|voyage|week-end|weekend|camping|"
    r"noel|halloween|paques|kermesse|remise|diplome|medaille|permis|repas|famille|mamie|papy|grand")
# … sauf la routine
ROUTINE = re.compile(r"\bcours\b|soutien|aide aux devoirs|travail|quart|rdv|rendez|medecin|dentiste|"
                     r"vaccin|garage|controle technique|poubelle|reunion|ferie|vacances scolaires|"
                     r"rappel|courrier lu|echeance|📬")
PHRASE_VOIX = re.compile(r"^.*?(garde|retiens|ajoute|note|mets)\s+(ce|ça|ca|comme|un|le|aux|dans les)?\s*"
                         r"(souvenir|moment|beau moment|souvenirs)s?\s*(de famille|du journal)?\s*[:,\-–]?\s*",
                         re.I)


# ------------------------------------------------------------------ outils purs (testés)

def texte_souvenir(phrase: str) -> str:
    """« ADRIA, garde ce souvenir : Aelia a eu sa médaille » -> « Aelia a eu sa médaille »."""
    t = PHRASE_VOIX.sub("", str(phrase or "").strip(), count=1).strip(" .")
    return (t[:1].upper() + t[1:])[:200] if len(t) >= 4 else ""


def titre_agenda(resume: str) -> str:
    """Ligne agenda du Core « 15/11/2026 11:00 Patin Aelia (lieu) » -> « Patin Aelia »."""
    t = re.sub(r"^\d{1,2}/\d{1,2}/\d{4}( \d{1,2}:\d{2})? ", "", str(resume or ""))
    return re.sub(r"\s*\([^)]*\)\s*$", "", t).strip()


def candidats(lignes: list[dict], refus: dict, deja: set, aujourdhui: date) -> list[dict]:
    """Lignes agenda {date, resume} -> 3 beaux moments au plus, un par titre, sans refus récents."""
    vus, res = set(), []
    for l in sorted(lignes, key=lambda l: l["date"]):
        titre = titre_agenda(l["resume"])
        n = normaliser(titre)
        if not titre or not BEAUX.search(n) or ROUTINE.search(n):
            continue
        k = cle_doublon(titre)
        r = refus.get(k)
        if k in vus or (l["date"], k) in deja or (r and date.fromisoformat(r) > aujourdhui - timedelta(days=REFUS_JOURS)):
            continue
        vus.add(k)
        res.append({"date": l["date"], "texte": titre[:120], "cle": k})
    return res[:MAX_PROPOSITIONS]


def livre_html(annee: int, souvenirs: list[dict], intro: str = "") -> str:
    par_mois: dict[int, list[dict]] = {}
    for s in sorted(souvenirs, key=lambda s: s["date"]):
        par_mois.setdefault(int(s["date"][5:7]), []).append(s)
    corps = []
    for m in sorted(par_mois):
        items = "".join(
            f"<li><b>{int(s['date'][8:10])} {MOIS[m - 1]}</b> — {html.escape(s['texte'])}</li>"
            for s in par_mois[m])
        corps.append(f"<h2>{MOIS[m - 1].capitalize()}</h2><ul>{items}</ul>")
    vide = "<p>Aucun souvenir gardé cette année.</p>" if not corps else ""
    return ("<!doctype html><html lang=fr><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Famille Kerherve — {annee}</title>"
            "<style>body{font-family:Georgia,serif;max-width:720px;margin:auto;padding:24px;"
            "color:#2b2b2b;background:#fbf8f2}h1{text-align:center}h2{color:#8a5a2b;"
            "border-bottom:1px solid #e3d6c3}li{margin:6px 0}.intro{font-style:italic;text-align:center}</style>"
            f"<h1>Famille Kerherve — {annee}</h1>"
            + (f"<p class=intro>{html.escape(intro)}</p>" if intro else "")
            + "".join(corps) + vide
            + f"<p style='text-align:center;color:#999'>Livre préparé par ADRIA le "
              f"{date.today().strftime('%d/%m/%Y')} — {len(souvenirs)} souvenir(s).</p></html>")


# --------------------------------------------------------------------------- l'agent

class AgentJournal:
    """`core` = le Core : memoire, etat(), appel(), routeur, notifier (via courriers)."""

    def __init__(self, core, notifier: str) -> None:
        self.core = core
        self.notifier = notifier
        self.db = core.memoire.db
        self.db.execute("""CREATE TABLE IF NOT EXISTS souvenirs (
            id INTEGER PRIMARY KEY, date TEXT NOT NULL, texte TEXT NOT NULL, origine TEXT,
            qui TEXT, ajoute_le TEXT NOT NULL)""")
        self.db.commit()
        self.dernier: dict | None = None

    # ---- base (jamais d'effacement)
    def _lire(self, cle, defaut):
        try:
            return json.loads(self.core.memoire.meta(cle) or "null") or defaut
        except ValueError:
            return defaut

    def _ecrire(self, cle, valeur) -> None:
        self.core.memoire.meta(cle, json.dumps(valeur, ensure_ascii=False))

    def garder(self, jour: str, texte: str, origine: str, qui: str = "") -> None:
        self.db.execute("INSERT INTO souvenirs (date, texte, origine, qui, ajoute_le) VALUES (?, ?, ?, ?, ?)",
                        (jour, texte, origine, qui, datetime.now().isoformat(timespec="seconds")))
        self.db.commit()
        self.core.memoire.ajouter("souvenirs_famille", [{
            "summary": f"Souvenir de famille du {jour[8:10]}/{jour[5:7]}/{jour[:4]} : {texte}", "date": jour}])

    def souvenirs(self, annee: int | None = None) -> list[dict]:
        req = "SELECT date, texte, origine FROM souvenirs"
        args = ()
        if annee:
            req += " WHERE substr(date, 1, 4) = ?"
            args = (str(annee),)
        return [{"date": r[0], "texte": r[1], "origine": r[2]} for r in self.db.execute(req + " ORDER BY date, id", args)]

    def agenda_semaine(self, aujourdhui: date) -> list[dict]:
        debut = (aujourdhui - timedelta(days=7)).isoformat()
        return [{"date": r[0], "resume": r[1]} for r in self.db.execute(
            "SELECT date_evt, resume FROM lignes WHERE source = 'agenda' AND date_evt >= ? AND date_evt < ?",
            (debut, aujourdhui.isoformat()))]

    async def notifier_tel(self, titre: str, message: str, actions=None, tag="adria_journal") -> None:
        domaine, service = self.notifier.split(".", 1)
        data = {"tag": tag, "channel": "ADRIA famille"}
        if actions:
            data["actions"] = actions
        await self.core.appel("POST", f"/services/{domaine}/{service}",
                              {"title": titre, "message": message, "data": data})

    # ---- dimanche : propositions
    async def proposer(self, aujourdhui: date | None = None, forcer: bool = False) -> dict:
        auj = aujourdhui or date.today()
        res = {"quand": datetime.now().isoformat(timespec="seconds"), "proposes": 0}
        if await self.core.etat(INTERRUPTEUR) != "on" or (auj.weekday() != 6 and not forcer):
            return res
        deja = {(s["date"], cle_doublon(s["texte"])) for s in self.souvenirs()}
        cands = candidats(self.agenda_semaine(auj), self._lire("journal_refus", {}), deja, auj)
        attentes = self._lire("journal_attentes", {})
        for c in cands:
            ident = uuid.uuid4().hex[:8]
            attentes[ident] = c
            j = date.fromisoformat(c["date"])
            await self.notifier_tel(
                "📖 Un souvenir pour le journal ?",
                f"{c['texte']} ({['lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.', 'dim.'][j.weekday()]} "
                f"{j.strftime('%d/%m')}). On le garde dans le livre de l'année ?",
                [{"action": f"ADRIA_SOUV_{ident}_OUI", "title": "OUI"},
                 {"action": f"ADRIA_SOUV_{ident}_NON", "title": "NON"}], tag=f"adria_journal_{ident}")
        self._ecrire("journal_attentes", attentes)
        res["proposes"] = len(cands)
        self.dernier = res
        return res

    def recevoir_action(self, data: dict) -> str | None:
        action = str(data.get("action") or "")
        if not action.startswith("ADRIA_SOUV_"):
            return None
        _, _, ident, choix = action.split("_", 3)
        attentes = self._lire("journal_attentes", {})
        c = attentes.pop(ident, None)
        if not c:
            return None
        self._ecrire("journal_attentes", attentes)
        if choix == "OUI":
            self.garder(c["date"], c["texte"], "agenda", "")
        else:
            refus = self._lire("journal_refus", {})
            refus[c["cle"]] = date.today().isoformat()
            self._ecrire("journal_refus", refus)
        return choix

    # ---- voix : « garde ce souvenir : … »
    async def garder_voix(self, data: dict) -> dict:
        res = {"req_id": data.get("req_id", ""), "ok": False, "texte": ""}
        if await self.core.etat(INTERRUPTEUR) != "on":
            res["texte"] = "Le journal de famille est éteint."
            return res
        t = texte_souvenir(data.get("texte") or "")
        if not t:
            res["texte"] = "Dis-moi le souvenir après « garde ce souvenir », par exemple : garde ce souvenir, Aelia a eu sa médaille."
            return res
        self.garder(date.today().isoformat(), t, "voix", str(data.get("qui") or ""))
        res.update(ok=True, texte=f"C'est gardé dans le livre de famille : {t}.")
        return res

    # ---- le livre de l'année
    async def livre(self, annee: int | None = None) -> dict:
        import classement
        annee = int(annee or date.today().year)
        souvenirs = self.souvenirs(annee)
        intro = ""
        if souvenirs:
            try:
                rep = await self.core.routeur.demander(
                    "Ecris UNE seule phrase chaleureuse (25 mots maximum, sans emoji) pour ouvrir le livre "
                    f"de famille {annee} de la famille Kerherve, a partir de ces moments : "
                    + " ; ".join(s["texte"] for s in souvenirs)[:2500], "rapide", "adria_core_livre")
                if rep.get("ok"):
                    intro = re.sub(r"\s+", " ", str(rep.get("data") or "")).strip().strip('"')[:250]
            except Exception as e:
                LOG.warning("Intro du livre : %s", type(e).__name__)
        dossier = os.path.join(classement.RACINE, *DOSSIER_LIVRE)
        os.makedirs(dossier, exist_ok=True)
        chemin = os.path.join(dossier, f"{date.today().isoformat()}_Livre_de_famille_{annee}.html")
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(livre_html(annee, souvenirs, intro))
        res = {"annee": annee, "souvenirs": len(souvenirs), "fichier": os.path.relpath(chemin, classement.RACINE)}
        await self.notifier_tel(f"📖 Le livre de famille {annee} est prêt",
                                f"{len(souvenirs)} souvenir(s), mois par mois. Dis « sors-moi le livre de famille » "
                                "ou ouvre la page ADRIA, « Retrouver un papier ».")
        self.dernier = res
        return res

    async def livre_si_15_decembre(self) -> None:
        if date.today().strftime("%m-%d") == "12-15" and await self.core.etat(INTERRUPTEUR) == "on":
            await self.livre()
