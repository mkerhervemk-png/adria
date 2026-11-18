"""Budget du foyer (lot B1), 0 IA en plus de la lecture du ticket déjà faite par le mode Courrier.

- Ticket de caisse ou facture payée envoyé en mode « Courrier » : le total est compté dans le budget
  (catégorie lue sur le ticket). Les abonnements suivis (lot A4) comptent pour leur part mensuelle.
- À la voix (Mael, Vanessa) : « j'ai dépensé 45 euros de courses chez Leclerc » -> dépense notée ;
  « combien on a dépensé ce mois-ci / en courses / le mois dernier ? » -> réponse calculée, 0 IA.
- Chaque jour à 10h : alerte si une catégorie du mois dépasse de 30 % (et 30 €) sa moyenne des
  3 mois précédents (une fois par catégorie et par mois).
- Le 1er du mois à 10h : bilan du mois écoulé par catégorie.
Pas de lien avec la banque, aucun numéro de compte ni de carte n'est lu ni gardé.
Aucune ligne n'est jamais effacée (règle du Core). Actif si input_boolean.adria_core_budget est ON.
"""

import logging
import re
from datetime import date, datetime, timedelta

from store import normaliser

LOG = logging.getLogger("adria_core.budget")

INTERRUPTEUR = "input_boolean.adria_core_budget"
CAPTEUR = "sensor.adria_core_budget"
CATEGORIES = ("courses", "carburant", "sante", "ecole", "loisirs", "maison", "vetements", "voiture",
              "restaurant", "cadeaux", "autre")
LIBELLES = {"courses": "courses", "carburant": "carburant", "sante": "santé", "ecole": "école",
            "loisirs": "loisirs", "maison": "maison", "vetements": "vêtements", "voiture": "voiture",
            "restaurant": "restaurant", "cadeaux": "cadeaux", "autre": "divers", "abonnements": "abonnements"}
MOTS = {
    "courses": r"course|supermarch|leclerc|carrefour|intermarch|lidl|aldi|super u|hyper|auchan|epicer|boulang|marche",
    "carburant": r"essence|gasoil|gazole|carburant|plein|station|recharge|borne",
    "sante": r"pharmac|medecin|docteur|dentiste|ophtal|kine|sante|mutuelle|ordonnance|optic",
    "ecole": r"ecole|cantine|fourniture|cahier|cartable|college|lycee|scolaire|sortie scolaire",
    "loisirs": r"loisir|cinema|patin|piscine|jeu|jouet|sport|licence|concert|parc|sortie|livre",
    "maison": r"maison|bricol|leroy|castorama|meuble|ikea|deco|jardin|electromenager|menage",
    "vetements": r"vetement|habit|chaussure|pull|pantalon|manteau|decathlon|kiabi",
    "voiture": r"garage|pneu|vidange|revision|controle technique|peage|parking|voiture|5008|inster",
    "restaurant": r"restau|resto|mcdo|burger|pizza|kebab|traiteur|cafe|bar",
    "cadeaux": r"cadeau|anniversaire|noel",
}
MOIS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout", "septembre", "octobre",
        "novembre", "decembre"]
SEUIL_POURCENT = 30.0
SEUIL_EUROS = 30.0

TEXTE_PROMPT = (
    '"depense": null, ou si c est un ticket de caisse, une note ou une facture DEJA PAYEE qui n est pas '
    'un abonnement : {"montant": total TTC paye (point decimal), "magasin": "nom court", '
    '"categorie": "' + "|".join(CATEGORIES) + '", "date": "AAAA-MM-JJ"}'
)


# ------------------------------------------------------------------ outils purs (testés)

def euros(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",").replace(",00", "") + " €"


def _montant(v) -> float | None:
    try:
        x = float(str(v).replace("€", "").replace(",", ".").replace(" ", "").strip())
    except (TypeError, ValueError):
        return None
    return round(x, 2) if 0 < x < 5000 else None


def categorie_de(texte: str) -> str | None:
    t = normaliser(texte)
    for cat, motif in MOTS.items():
        if re.search(motif, t):
            return cat
    return None


def analyser_depense(d, aujourdhui: date) -> dict | None:
    """Bloc « depense » de Gemini -> dépense propre, ou None."""
    if not isinstance(d, dict):
        return None
    montant = _montant(d.get("montant"))
    if montant is None:
        return None
    try:
        jour = date.fromisoformat(str(d.get("date") or "")[:10])
    except ValueError:
        jour = aujourdhui
    if jour > aujourdhui or jour < aujourdhui - timedelta(days=400):
        jour = aujourdhui
    cat = normaliser(str(d.get("categorie") or "")).strip()
    magasin = re.sub(r"\s+", " ", str(d.get("magasin") or "")).strip()[:40]
    if cat not in CATEGORIES:
        cat = categorie_de(magasin) or "autre"
    return {"jour": jour, "montant": montant, "categorie": cat, "magasin": magasin}


MONTANT_VOIX = re.compile(r"(\d+(?:[.,]\d{1,2})?)\s*(?:€|euros?|eur\b|balles)(?:\s*(\d{1,2})\b)?")
DEPENSE_VOIX = re.compile(r"(j'ai|on a|nous avons|vanessa a|mael a)\s+(depense|paye|achete|mis|claque|fait le plein)")


def depense_vocale(phrase: str, aujourdhui: date) -> dict | None:
    """« j'ai dépensé 45,30 € de courses chez Leclerc » -> dépense, ou None."""
    t = normaliser(phrase).replace("’", "'")
    if not DEPENSE_VOIX.search(t):
        return None
    m = MONTANT_VOIX.search(t)
    if not m:
        return None
    brut = m.group(1).replace(",", ".")
    if m.group(2) and "." not in brut:  # « 45 euros 30 »
        brut = f"{brut}.{int(m.group(2)):02d}"
    montant = _montant(brut)
    if montant is None:
        return None
    jour = aujourdhui - timedelta(days=1) if re.search(r"\bhier\b", t) else aujourdhui
    mg = re.search(r"\bchez\s+([a-z0-9' ]{2,25}?)(?:\s+(?:hier|aujourd|ce matin|pour|de|en|a)\b|[.,!]|$)", t)
    magasin = mg.group(1).strip().title() if mg else ""
    reste = t[:m.start()] + " " + t[m.end():]
    return {"jour": jour, "montant": montant, "categorie": categorie_de(reste) or "autre", "magasin": magasin}


def mois_suivant(a: int, m: int, n: int = 1) -> tuple[int, int]:
    k = a * 12 + (m - 1) + n
    return k // 12, k % 12 + 1


def totaux(depenses: list[dict], a: int, m: int) -> dict[str, float]:
    t: dict[str, float] = {}
    for d in depenses:
        if d["jour"].year == a and d["jour"].month == m:
            t[d["categorie"]] = round(t.get(d["categorie"], 0) + d["montant"], 2)
    return t


def moyenne_3_mois(depenses: list[dict], a: int, m: int) -> dict[str, float]:
    """Moyenne des 3 mois avant (a, m), sur les mois qui ont au moins une dépense."""
    mois = [mois_suivant(a, m, -k) for k in (1, 2, 3)]
    actifs = [x for x in mois if any(d["jour"].year == x[0] and d["jour"].month == x[1] for d in depenses)]
    if not actifs:
        return {}
    somme: dict[str, float] = {}
    for x in actifs:
        for c, v in totaux(depenses, *x).items():
            somme[c] = somme.get(c, 0) + v
    return {c: round(v / len(actifs), 2) for c, v in somme.items()}


def alertes(depenses: list[dict], aujourdhui: date, deja: set[str]) -> list[tuple[str, str]]:
    """[(clé, message)] pour les catégories du mois nettement au-dessus de leur moyenne."""
    a, m = aujourdhui.year, aujourdhui.month
    moy = moyenne_3_mois(depenses, a, m)
    out = []
    for c, v in sorted(totaux(depenses, a, m).items()):
        ref = moy.get(c, 0)
        cle = f"{a}-{m:02d}-{c}"
        if ref <= 0 or cle in deja:
            continue
        if v - ref >= SEUIL_EUROS and (v - ref) / ref * 100 >= SEUIL_POURCENT:
            out.append((cle, f"💶 {LIBELLES[c].capitalize()} : déjà {euros(v)} ce mois-ci, pour "
                             f"{euros(ref)} en moyenne les mois précédents (+{(v - ref) / ref * 100:.0f} %)."))
    return out


def libelle_mois(a: int, m: int) -> str:
    noms = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre",
            "octobre", "novembre", "décembre"]
    return f"{noms[m - 1]} {a}"


def bilan(depenses: list[dict], abonnements_mois: float, a: int, m: int) -> str:
    t = totaux(depenses, a, m)
    total = round(sum(t.values()) + abonnements_mois, 2)
    lignes = [f"Total {libelle_mois(a, m)} : {euros(total)}"]
    if abonnements_mois:
        lignes.append(f"• abonnements : {euros(abonnements_mois)}")
    lignes += [f"• {LIBELLES[c]} : {euros(v)}" for c, v in sorted(t.items(), key=lambda x: -x[1])]
    pa, pm = mois_suivant(a, m, -1)
    avant = totaux(depenses, pa, pm)
    if avant:
        ecart = sum(t.values()) - sum(avant.values())
        lignes.append(f"Dépenses notées : {'+' if ecart >= 0 else '−'}{euros(abs(ecart))} par rapport à "
                      f"{libelle_mois(pa, pm)}.")
    if not t:
        lignes.append("Aucun ticket noté ce mois-là : envoie tes tickets en mode Courrier ou dis-moi "
                      "« j'ai dépensé … ».")
    return "\n".join(lignes)


def periode_question(question: str, aujourdhui: date) -> tuple[int, int]:
    t = normaliser(question)
    if re.search(r"mois (dernier|passe|precedent)", t):
        return mois_suivant(aujourdhui.year, aujourdhui.month, -1)
    for i, nom in enumerate(MOIS, 1):
        if re.search(rf"\b{nom}\b", t):
            a = aujourdhui.year if i <= aujourdhui.month else aujourdhui.year - 1
            return a, i
    return aujourdhui.year, aujourdhui.month


def repondre(question: str, depenses: list[dict], abonnements_mois: float, aujourdhui: date) -> str:
    a, m = periode_question(question, aujourdhui)
    t = totaux(depenses, a, m)
    cat = categorie_de(question)
    quand = "ce mois-ci" if (a, m) == (aujourdhui.year, aujourdhui.month) else f"en {libelle_mois(a, m)}"
    if cat:
        v = t.get(cat, 0)
        return (f"En {LIBELLES[cat]}, vous avez dépensé {euros(v)} {quand}, d'après les tickets notés."
                if v else f"Je n'ai aucun ticket de {LIBELLES[cat]} noté {quand}.")
    if re.search(r"abonnement", normaliser(question)):
        return f"Les abonnements suivis coûtent {euros(abonnements_mois)} par mois."
    total = round(sum(t.values()) + abonnements_mois, 2)
    if not t and not abonnements_mois:
        return f"Je n'ai encore aucune dépense notée {quand}."
    top = ", ".join(f"{LIBELLES[c]} {euros(v)}" for c, v in sorted(t.items(), key=lambda x: -x[1])[:3])
    return (f"{quand.capitalize()}, j'ai noté {euros(total)} : abonnements {euros(abonnements_mois)}"
            + (f", puis {top}." if top else ".") + " Seuls les tickets que tu m'as donnés sont comptés.")


# --------------------------------------------------------------------- stockage (même base)

class Budget:
    """Table `depenses`. Aucune ligne n'est jamais retirée."""

    def __init__(self, memoire) -> None:
        self.memoire = memoire
        self.db = memoire.db
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS depenses (
                id INTEGER PRIMARY KEY, jour TEXT NOT NULL, montant REAL NOT NULL, categorie TEXT NOT NULL,
                magasin TEXT, pour TEXT, origine TEXT, ajoute_le TEXT NOT NULL
            );
            """)
        self.db.commit()

    def depenses(self) -> list[dict]:
        return [{"jour": date.fromisoformat(r["jour"]), "montant": r["montant"], "categorie": r["categorie"],
                 "magasin": r["magasin"] or ""} for r in self.db.execute("SELECT * FROM depenses ORDER BY jour, id")]

    def deja(self, d: dict) -> bool:
        """Même ticket déjà compté (même jour, même montant, même magasin)."""
        return self.db.execute("SELECT 1 FROM depenses WHERE jour = ? AND montant = ? AND IFNULL(magasin,'') = ?",
                               (d["jour"].isoformat(), d["montant"], d.get("magasin") or "")).fetchone() is not None

    def ajouter(self, d: dict, pour: str = "", origine: str = "", maintenant: datetime | None = None) -> bool:
        if self.deja(d):
            return False
        quand = (maintenant or datetime.now()).isoformat(timespec="seconds")
        self.db.execute("INSERT INTO depenses (jour, montant, categorie, magasin, pour, origine, ajoute_le)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (d["jour"].isoformat(), d["montant"], d["categorie"], d.get("magasin") or "", pour,
                         origine, quand))
        self.db.commit()
        chez = f" chez {d['magasin']}" if d.get("magasin") else ""
        self.memoire.ajouter("budget", [{
            "summary": f"Dépense {LIBELLES[d['categorie']]} {euros(d['montant'])}{chez} le "
                       f"{d['jour'].strftime('%d/%m/%Y')} ({origine or 'ticket'}).",
            "date": d["jour"].isoformat()}], maintenant)
        return True

    def etat(self, abonnements_mois: float, aujourdhui: date | None = None) -> dict:
        j = aujourdhui or date.today()
        dep = self.depenses()
        t = totaux(dep, j.year, j.month)
        pa, pm = mois_suivant(j.year, j.month, -1)
        return {
            "state": round(sum(t.values()) + abonnements_mois, 2),
            "attributes": {
                "friendly_name": "ADRIA — budget du mois (€)", "icon": "mdi:wallet-outline",
                "unit_of_measurement": "€", "mois": libelle_mois(j.year, j.month),
                "abonnements_par_mois": abonnements_mois,
                "par_categorie": {LIBELLES[c]: v for c, v in sorted(t.items(), key=lambda x: -x[1])},
                "mois_precedent": round(sum(totaux(dep, pa, pm).values()) + abonnements_mois, 2),
                "moyenne_3_mois": {LIBELLES[c]: v for c, v in moyenne_3_mois(dep, j.year, j.month).items()},
                "tickets_notes": len(dep),
            }}

    def resume(self, abonnements_mois: float, aujourdhui: date | None = None) -> str:
        j = aujourdhui or date.today()
        return repondre("", self.depenses(), abonnements_mois, j)
