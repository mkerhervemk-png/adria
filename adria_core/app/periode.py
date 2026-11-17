"""Repère une période dans une question en français (0 IA).

Renvoie (debut, fin) inclusifs au format date, ou None.
Exemples : « en octobre », « octobre 2026 », « la semaine dernière », « cette semaine »,
« le mois dernier », « hier », « avant-hier », « le 24/10 », « le 24 octobre »,
« mardi dernier », « le week-end dernier », « ce mois-ci ».
"""

import re
from datetime import date, timedelta

from store import normaliser

MOIS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
        "septembre", "octobre", "novembre", "decembre"]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _fin_mois(a: int, m: int) -> date:
    return (date(a + (m == 12), m % 12 + 1, 1) - timedelta(days=1))


def _annee_passee(m: int, j: int, auj: date) -> int:
    """Année la plus récente où (m, j) n'est pas dans le futur."""
    try:
        return auj.year if date(auj.year, m, j) <= auj else auj.year - 1
    except ValueError:
        return auj.year


def periode(question: str, auj: date | None = None) -> tuple[date, date] | None:
    auj = auj or date.today()
    q = normaliser(question)
    lundi = auj - timedelta(days=auj.weekday())

    if re.search(r"avant[- ]hier", q):
        d = auj - timedelta(days=2)
        return d, d
    if re.search(r"\bhier\b", q):
        d = auj - timedelta(days=1)
        return d, d
    if re.search(r"semaine (derniere|passee)|la semaine d.avant", q):
        return lundi - timedelta(days=7), lundi - timedelta(days=1)
    if re.search(r"cette semaine", q):
        return lundi, auj
    if re.search(r"week-?end (dernier|passe)|dernier week-?end", q):
        sam = lundi - timedelta(days=2)
        return sam, sam + timedelta(days=1)
    if re.search(r"mois (dernier|passe)", q):
        premier = auj.replace(day=1)
        fin = premier - timedelta(days=1)
        return fin.replace(day=1), fin
    if re.search(r"ce mois(-ci)?\b", q):
        return auj.replace(day=1), auj

    m = re.search(r"\b(" + "|".join(JOURS) + r") (dernier|passe)\b", q)
    if m:
        cible = JOURS.index(m.group(1))
        ecart = (auj.weekday() - cible) % 7 or 7
        d = auj - timedelta(days=ecart)
        return d, d

    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", q)
    if m:
        j, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12 and 1 <= j <= 31:
            a = int(m.group(3)) if m.group(3) else _annee_passee(mo, j, auj)
            a = a + 2000 if a < 100 else a
            try:
                d = date(a, mo, j)
                return d, d
            except ValueError:
                pass

    m = re.search(r"\b(\d{1,2}) (" + "|".join(MOIS) + r")(?: (\d{4}))?\b", q)
    if m:
        j, mo = int(m.group(1)), MOIS.index(m.group(2)) + 1
        a = int(m.group(3)) if m.group(3) else _annee_passee(mo, j, auj)
        try:
            d = date(a, mo, j)
            return d, d
        except ValueError:
            pass

    m = re.search(r"\b(" + "|".join(MOIS) + r")(?: (\d{4}))?\b", q)
    if m:
        mo = MOIS.index(m.group(1)) + 1
        a = int(m.group(2)) if m.group(2) else (auj.year if mo <= auj.month else auj.year - 1)
        return date(a, mo, 1), min(_fin_mois(a, mo), auj)
    return None
