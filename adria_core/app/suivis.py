"""Suivi des abonnements (lot A4) et des garanties (lot A5), 0 IA en plus de la lecture du courrier.

A4 — Abonnements et factures : quand le courrier lu est une facture récurrente (box, mobile, énergie,
assurance…), ADRIA propose « Suivre cet abonnement ? ». Au OUI, elle garde fournisseur, service,
montant, périodicité et fin d'engagement. Elle signale :
- une HAUSSE par rapport à la facture précédente du même fournisseur ;
- un possible DOUBLON (deux fournisseurs actifs pour la même box, la même énergie, la même assurance
  habitation ou le même streaming) ;
- la FIN D'ENGAGEMENT, 30 jours avant (bon moment pour comparer ou résilier).
A5 — Garanties : quand c'est un ticket de caisse ou une facture d'achat, ADRIA propose « Suivre la
garantie ? » (durée écrite sur le ticket, sinon garantie légale de 2 ans). Rappel 30 jours avant la fin.

Rien sans ton OUI. Rien n'est jamais effacé (règle du Core) : un abonnement résilié disparaît seulement
du total quand ses factures s'arrêtent. Actif si input_boolean.adria_core_suivis est ON.
"""

import logging
import re
import unicodedata
from datetime import date, datetime, timedelta

LOG = logging.getLogger("adria_core.suivis")

INTERRUPTEUR = "input_boolean.adria_core_suivis"
CAPTEUR = "sensor.adria_core_abonnements"
PERIODES = {"mensuel": 1, "bimestriel": 2, "trimestriel": 3, "semestriel": 6, "annuel": 12}
SUFFIXES = {"mensuel": "/mois", "bimestriel": " tous les 2 mois", "trimestriel": "/trimestre",
            "semestriel": "/semestre", "annuel": "/an"}
SERVICES = ("box", "mobile", "energie", "eau", "assurance_auto", "assurance_habitation",
            "assurance_sante", "streaming", "sport", "banque", "autre")
DOUBLONS_POSSIBLES = {"box", "energie", "assurance_habitation", "streaming"}
GARANTIE_LEGALE_MOIS = 24
PREAVIS_JOURS = 30
HAUSSE_MIN_POURCENT = 2.0
HAUSSE_MIN_EUROS = 0.5

TEXTE_PROMPT = (
    '"facture": null, ou si c est une facture ou un echeancier d un abonnement qui revient '
    '(box, mobile, energie, eau, assurance, streaming, salle de sport, banque) : '
    '{"fournisseur": "nom court", "service": "' + "|".join(SERVICES) + '", "date": "AAAA-MM-JJ date de la facture", '
    '"montant": nombre TTC a payer pour la periode (point decimal), '
    '"periodicite": "' + "|".join(PERIODES) + '", "fin_engagement": "AAAA-MM-JJ ou vide"}, '
    '"achat": null, ou si c est un ticket de caisse ou une facture d achat d un objet : '
    '{"appareil": "objet achete, marque et type, 5 mots maximum", "magasin": "nom court", '
    '"date_achat": "AAAA-MM-JJ", "garantie_mois": nombre de mois de garantie ECRIT sur le document, '
    '0 si rien n est ecrit}'
)


# ------------------------------------------------------------------ outils purs (testés)

def cle(t: str) -> str:
    t = unicodedata.normalize("NFD", str(t or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def ajouter_mois(jour: date, mois: int) -> date:
    m = jour.month - 1 + mois
    an, m = jour.year + m // 12, m % 12 + 1
    for j in (jour.day, 30, 29, 28):
        try:
            return date(an, m, j)
        except ValueError:
            continue
    return date(an, m, 28)


def _montant(v) -> float | None:
    try:
        x = float(str(v).replace("€", "").replace(",", ".").replace(" ", "").strip())
    except (TypeError, ValueError):
        return None
    return round(x, 2) if 0 < x < 10000 else None


def _date(v) -> date | None:
    try:
        return date.fromisoformat(str(v or "").strip()[:10])
    except ValueError:
        return None


def euros(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",") + " €"


def analyser_facture(d) -> dict | None:
    """Bloc « facture » de Gemini -> facture propre, ou None si ce n'est pas un abonnement."""
    if not isinstance(d, dict):
        return None
    fournisseur = re.sub(r"\s+", " ", str(d.get("fournisseur") or "")).strip()[:40]
    montant = _montant(d.get("montant"))
    periodicite = cle(d.get("periodicite"))
    if not fournisseur or montant is None or periodicite not in PERIODES:
        return None
    service = cle(d.get("service")).replace(" ", "_")
    return {"fournisseur": fournisseur, "cle": cle(fournisseur),
            "service": service if service in SERVICES else "autre",
            "montant": montant, "periodicite": periodicite, "mois": PERIODES[periodicite],
            "fin_engagement": _date(d.get("fin_engagement")), "date_doc": _date(d.get("date"))}


def analyser_achat(d, aujourdhui: date) -> dict | None:
    """Bloc « achat » de Gemini -> garantie propre, ou None."""
    if not isinstance(d, dict):
        return None
    appareil = re.sub(r"\s+", " ", str(d.get("appareil") or "")).strip()[:50]
    achat = _date(d.get("date_achat"))
    if not appareil or achat is None or achat > aujourdhui or achat < aujourdhui - timedelta(days=3650):
        return None
    try:
        mois = int(float(d.get("garantie_mois") or 0))
    except (TypeError, ValueError):
        mois = 0
    legale = not 1 <= mois <= 120
    mois = GARANTIE_LEGALE_MOIS if legale else mois
    return {"appareil": appareil, "magasin": re.sub(r"\s+", " ", str(d.get("magasin") or "")).strip()[:40],
            "date_achat": achat, "mois": mois, "legale": legale, "fin": ajouter_mois(achat, mois)}


def hausse(precedente: dict | None, nouvelle: dict) -> str:
    """Texte d'alerte si le montant a augmenté (même périodicité), sinon ''."""
    if not precedente or precedente.get("periodicite") != nouvelle["periodicite"]:
        return ""
    avant, apres = float(precedente["montant"]), nouvelle["montant"]
    ecart = apres - avant
    if ecart < HAUSSE_MIN_EUROS or ecart / avant * 100 < HAUSSE_MIN_POURCENT:
        return ""
    return (f"⚠️ Hausse : {euros(avant)} → {euros(apres)} (+{ecart / avant * 100:.0f} %) "
            f"depuis la facture du {precedente['date_doc'].strftime('%d/%m/%Y')}.")


def actifs(factures: list[dict], aujourdhui: date) -> list[dict]:
    """Dernière facture de chaque fournisseur, si elle n'est pas trop ancienne pour sa périodicité."""
    derniere: dict[str, dict] = {}
    for f in factures:
        if f["cle"] not in derniere or f["date_doc"] >= derniere[f["cle"]]["date_doc"]:
            derniere[f["cle"]] = f
    limite = lambda f: f["date_doc"] + timedelta(days=(2 * f["mois"] + 1) * 31)  # noqa: E731
    return sorted((f for f in derniere.values() if limite(f) >= aujourdhui),
                  key=lambda f: (-f["montant"] / f["mois"], f["fournisseur"]))


def doublons(actives: list[dict], nouvelle: dict) -> list[str]:
    if nouvelle["service"] not in DOUBLONS_POSSIBLES:
        return []
    return sorted({f["fournisseur"] for f in actives
                   if f["service"] == nouvelle["service"] and f["cle"] != nouvelle["cle"]})


def par_mois(f: dict) -> float:
    return round(f["montant"] / f["mois"], 2)


def libelle_facture(f: dict) -> str:
    return (f"{f['fournisseur']} ({f['service'].replace('_', ' ')}) : {euros(f['montant'])}"
            + SUFFIXES[f["periodicite"]])


def libelle_garantie(g: dict) -> str:
    chez = f" ({g['magasin']})" if g.get("magasin") else ""
    return (f"{g['appareil']}{chez} : garantie jusqu'au {g['fin'].strftime('%d/%m/%Y')}"
            + (" (garantie légale 2 ans)" if g.get("legale") else f" ({g['mois']} mois)"))


def rappels(factures: list[dict], garanties: list[dict], aujourdhui: date) -> list[str]:
    """Messages du jour : fin d'engagement ou fin de garantie dans exactement 30 jours."""
    cible = aujourdhui + timedelta(days=PREAVIS_JOURS)
    msgs = [f"📑 Fin d'engagement {f['fournisseur']} le {cible.strftime('%d/%m/%Y')} : bon moment pour "
            "comparer ou résilier." for f in actifs(factures, aujourdhui) if f.get("fin_engagement") == cible]
    msgs += [f"🛡️ La garantie de {g['appareil']} se termine le {cible.strftime('%d/%m/%Y')}."
             for g in garanties if g["fin"] == cible]
    return msgs


# --------------------------------------------------------------------- stockage (même base)

class Suivis:
    """Tables `factures` et `garanties` dans la base du Core. Aucune ligne n'est jamais retirée."""

    def __init__(self, memoire) -> None:
        self.memoire = memoire
        self.db = memoire.db
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS factures (
                id INTEGER PRIMARY KEY, cle TEXT NOT NULL, fournisseur TEXT NOT NULL, service TEXT,
                montant REAL NOT NULL, periodicite TEXT NOT NULL, mois INTEGER NOT NULL,
                date_doc TEXT NOT NULL, fin_engagement TEXT, ajoute_le TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS garanties (
                id INTEGER PRIMARY KEY, appareil TEXT NOT NULL, magasin TEXT, date_achat TEXT NOT NULL,
                mois INTEGER NOT NULL, legale INTEGER NOT NULL, fin TEXT NOT NULL, pour TEXT,
                fichier TEXT, ajoute_le TEXT NOT NULL
            );
            """)
        self.db.commit()

    @staticmethod
    def _f(r) -> dict:
        return {"cle": r["cle"], "fournisseur": r["fournisseur"], "service": r["service"],
                "montant": r["montant"], "periodicite": r["periodicite"], "mois": r["mois"],
                "date_doc": date.fromisoformat(r["date_doc"]),
                "fin_engagement": _date(r["fin_engagement"])}

    @staticmethod
    def _g(r) -> dict:
        return {"appareil": r["appareil"], "magasin": r["magasin"] or "",
                "date_achat": date.fromisoformat(r["date_achat"]), "mois": r["mois"],
                "legale": bool(r["legale"]), "fin": date.fromisoformat(r["fin"]),
                "pour": r["pour"] or "", "fichier": r["fichier"] or ""}

    def factures(self) -> list[dict]:
        return [self._f(r) for r in self.db.execute("SELECT * FROM factures ORDER BY date_doc, id")]

    def garanties(self) -> list[dict]:
        return [self._g(r) for r in self.db.execute("SELECT * FROM garanties ORDER BY fin, id")]

    def precedente(self, cle_fournisseur: str) -> dict | None:
        r = self.db.execute("SELECT * FROM factures WHERE cle = ? ORDER BY date_doc DESC, id DESC LIMIT 1",
                            (cle_fournisseur,)).fetchone()
        return self._f(r) if r else None

    def ajouter_facture(self, f: dict, date_doc: date, maintenant: datetime | None = None) -> None:
        quand = (maintenant or datetime.now()).isoformat(timespec="seconds")
        self.db.execute(
            "INSERT INTO factures (cle, fournisseur, service, montant, periodicite, mois, date_doc,"
            " fin_engagement, ajoute_le) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f["cle"], f["fournisseur"], f["service"], f["montant"], f["periodicite"], f["mois"],
             date_doc.isoformat(), f["fin_engagement"].isoformat() if f.get("fin_engagement") else None,
             quand))
        self.db.commit()
        fin = (f" Fin d'engagement : {f['fin_engagement'].strftime('%d/%m/%Y')}."
               if f.get("fin_engagement") else "")
        self.memoire.ajouter("suivis", [{
            "summary": f"Abonnement {libelle_facture(f)}, facture du {date_doc.strftime('%d/%m/%Y')}.{fin}",
            "date": date_doc.isoformat()}], maintenant)

    def ajouter_garantie(self, g: dict, pour: str, fichier: str, maintenant: datetime | None = None) -> None:
        quand = (maintenant or datetime.now()).isoformat(timespec="seconds")
        self.db.execute(
            "INSERT INTO garanties (appareil, magasin, date_achat, mois, legale, fin, pour, fichier, ajoute_le)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (g["appareil"], g["magasin"], g["date_achat"].isoformat(), g["mois"], int(g["legale"]),
             g["fin"].isoformat(), pour, fichier, quand))
        self.db.commit()
        self.memoire.ajouter("suivis", [{
            "summary": f"Garantie {libelle_garantie(g)}, acheté le {g['date_achat'].strftime('%d/%m/%Y')}"
                       + (f", papier classé {fichier}" if fichier else "") + ".",
            "date": g["date_achat"].isoformat()}], maintenant)

    def etat(self, aujourdhui: date | None = None) -> dict:
        jour = aujourdhui or date.today()
        actives = actifs(self.factures(), jour)
        garanties = [g for g in self.garanties() if g["fin"] >= jour]
        return {
            "state": round(sum(par_mois(f) for f in actives), 2),
            "attributes": {
                "friendly_name": "ADRIA — abonnements suivis (€/mois)", "icon": "mdi:receipt-text-clock",
                "unit_of_measurement": "€",
                "abonnements": [{"fournisseur": f["fournisseur"], "service": f["service"],
                                 "montant": f["montant"], "periodicite": f["periodicite"],
                                 "par_mois": par_mois(f), "derniere_facture": f["date_doc"].isoformat(),
                                 "fin_engagement": f["fin_engagement"].isoformat() if f["fin_engagement"] else None}
                                for f in actives],
                "garanties": [{"appareil": g["appareil"], "magasin": g["magasin"], "pour": g["pour"],
                               "fin": g["fin"].isoformat(), "legale": g["legale"]} for g in garanties],
            }}
