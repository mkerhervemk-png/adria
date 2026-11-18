"""Purges des listes ADRIA de Home Assistant (lot M2b-1), faites par le Core.

Reprend à l'identique les règles des purges HA :
- O5  : todo.adria_journal — lignes de plus de 48 h, lignes sans horodatage hors aujourd'hui/hier,
        plafond 200 lignes horodatées (les plus anciennes partent).
- O12 : todo.adria_memoire_perso — lignes [personne|jj/mm] de plus de 7 jours ;
        todo.adria_memoire — lignes [calendrier]/[obs] dont la date jj/mm est passée.
- Archiveur : épisodes, journal intérieur, observations, souvenirs — au-delà de 15 jours.

Ces fonctions ne font que CHOISIR les lignes. Le Core ne retire ensuite de HA que celles dont il a
déjà la copie dans sa base. La base du Core, elle, n'est jamais purgée.
"""

import re
from datetime import date, datetime, timedelta

LISTES_15J = ("todo.adria_jarvis_episodes", "todo.adria_journal_interieur",
              "todo.adria_observations", "todo.adria_souvenirs")
LISTES = ("todo.adria_journal", "todo.adria_memoire_perso", "todo.adria_memoire") + LISTES_15J


def _jour_mois(jour: int, mois: int, annee: int) -> date | None:
    try:
        return date(annee, mois, jour)
    except ValueError:  # 29/02 hors année bissextile, date absurde
        return None


def date_ligne(item: dict, auj: date) -> date | None:
    """Même logique que la macro dt() de script.adria_memoire_longue_archiver."""
    d0 = str(item.get("description") or "")
    f = d0.split("|")[0].split(";")[0].strip()
    s = item.get("summary") or ""
    if re.fullmatch(r"[0-9]{9,}", f):
        return datetime.fromtimestamp(int(f)).date()
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", f):
        try:
            return date.fromisoformat(f)
        except ValueError:
            return None
    m = re.search(r"dt=([0-9]{4}-[0-9]{2}-[0-9]{2})", d0)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None
    m = re.search(r"([0-9]{2})/([0-9]{2})/([0-9]{4})", s)
    if m:
        return _jour_mois(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"([0-9]{2})/([0-9]{2})", s)
    if m:
        d = _jour_mois(int(m.group(1)), int(m.group(2)), auj.year)
        if d and d > auj:
            d = _jour_mois(int(m.group(1)), int(m.group(2)), auj.year - 1)
        return d
    return None


def _ouverte(item: dict) -> bool:
    return (item.get("status") or "needs_action") == "needs_action"


def purge_journal(items: list[dict], maintenant: datetime) -> list[dict]:
    """O5 : 48 h, sans horodatage hors aujourd'hui/hier, plafond 200."""
    lim = maintenant.timestamp() - 172800
    d1 = maintenant.strftime("%d/%m")
    d2 = (maintenant - timedelta(days=1)).strftime("%d/%m")
    vieux, gardes = [], []
    for i in items:
        if not _ouverte(i):
            continue
        d = str(i.get("description") or "").split("|")[0]
        if re.fullmatch(r"[0-9]{9,}", d):
            if int(d) < lim:
                vieux.append(i)
            else:
                gardes.append((int(d), i))
        elif d1 not in (i.get("summary") or "") and d2 not in (i.get("summary") or ""):
            vieux.append(i)
    gardes.sort(key=lambda x: x[0])
    if len(gardes) > 200:
        vieux += [i for _, i in gardes[:len(gardes) - 200]]
    return vieux


def purge_memoire_perso(items: list[dict], maintenant: datetime) -> list[dict]:
    """O12 : [personne|jj/mm] de plus de 7 jours (toutes lignes, comme l'automatisation)."""
    minuit = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
    res = []
    for i in items:
        m = re.match(r"^\[[a-z]+\|(\d\d)/(\d\d)\]", i.get("summary") or "")
        if not m:
            continue
        try:
            d = minuit.replace(month=int(m.group(2)), day=int(m.group(1)))
            if d > maintenant:
                d = d.replace(year=d.year - 1)
        except ValueError:
            continue
        if (maintenant - d).days > 7:
            res.append(i)
    return res


def purge_memoire(items: list[dict], maintenant: datetime) -> list[dict]:
    """O12 (06/11) : [calendrier]/[obs] dont la date jj/mm est passée."""
    auj = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
    res = []
    for i in items:
        s = i.get("summary") or ""
        if not _ouverte(i) or not re.search(r"\[(calendrier|obs)\]", s):
            continue
        m = re.search(r"(\d\d)/(\d\d)", s)
        if not m:
            continue
        try:
            d = auj.replace(month=int(m.group(2)), day=int(m.group(1)))
            # 18/11 : année la plus proche d'aujourd'hui (un 05/01 vu en novembre = janvier prochain)
            if d < auj - timedelta(days=182):
                d = d.replace(year=d.year + 1)
            elif d > auj + timedelta(days=182):
                d = d.replace(year=d.year - 1)
        except ValueError:
            continue
        if d < auj:
            res.append(i)
    return res


def purge_15_jours(items: list[dict], maintenant: datetime) -> list[dict]:
    """Archiveur : lignes datées de plus de 15 jours."""
    auj = maintenant.date()
    res = []
    for i in items:
        if not _ouverte(i):
            continue
        d = date_ligne(i, auj)
        if d and (auj - d).days > 15:
            res.append(i)
    return res


def a_purger(liste: str, items: list[dict], maintenant: datetime) -> list[dict]:
    if liste == "todo.adria_journal":
        return purge_journal(items, maintenant)
    if liste == "todo.adria_memoire_perso":
        return purge_memoire_perso(items, maintenant)
    if liste == "todo.adria_memoire":
        return purge_memoire(items, maintenant)
    if liste in LISTES_15J:
        return purge_15_jours(items, maintenant)
    return []
