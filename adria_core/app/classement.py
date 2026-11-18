"""Classement des papiers traités (lot A3) : copie rangée par personne puis par type.

/media/adria_documents/<Personne>/<Type>/<AAAA-MM-JJ>_<titre>.<ext>, puis copie OneDrive
(Apps/Home Assistant/ADRIA/documents/<Personne>/<Type>). Rien n'est jamais effacé : ce sont des copies,
un nom déjà pris reçoit un suffixe _2, _3…
Actif seulement si input_boolean.adria_core_classement est ON.
"""

import logging
import os
import re
import shutil
import unicodedata
from datetime import date

LOG = logging.getLogger("adria_core.classement")

RACINE = "/media/adria_documents"
DOSSIER_ONEDRIVE = "ADRIA/documents"
INTERRUPTEUR = "input_boolean.adria_core_classement"
CATEGORIES = {"ecole": "École", "sante": "Santé", "assurance": "Assurance", "impots": "Impôts",
              "banque": "Banque", "voiture": "Voiture", "maison": "Maison", "sport": "Sport",
              "travail": "Travail", "facture": "Factures", "achat": "Achats", "autre": "Autre"}
TEXTE_CATEGORIES = "|".join(CATEGORIES)


def _cle(t: str) -> str:
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn").strip()


def categorie(valeur) -> str:
    """Valeur Gemini -> nom de dossier (Autre si inconnue)."""
    c = _cle(valeur)
    return CATEGORIES.get(c) or next((v for k, v in CATEGORIES.items() if c.startswith(k)), "Autre")


def propre(t: str, defaut: str, n: int = 60) -> str:
    t = re.sub(r"[^\w\-]+", "_", str(t or "")).strip("_.")[:n]
    return t or defaut


def chemin_libre(dossier: str, base: str, ext: str) -> str:
    chemin = os.path.join(dossier, base + ext)
    for i in range(2, 100):
        if not os.path.exists(chemin):
            return chemin
        chemin = os.path.join(dossier, f"{base}_{i}{ext}")
    return chemin


def destination(personne: str, cat: str, jour: date, titre: str, ext: str, racine: str | None = None) -> str:
    dossier = os.path.join(racine or RACINE, propre(personne, "Foyer", 30), categorie(cat))
    return chemin_libre(dossier, f"{jour.isoformat()}_{propre(titre, 'document')}", ext.lower())


async def classer(ha, source: str, personne: str, cat: str, titre: str, onedrive_entree: str | None,
                  jour: date | None = None) -> dict | None:
    """Copie rangée + OneDrive. Retourne {'dossier', 'onedrive'} ou None si l'interrupteur est OFF."""
    if await ha.etat(INTERRUPTEUR) != "on":
        return None
    dest = destination(personne, cat, jour or date.today(), titre, os.path.splitext(source)[1] or ".pdf")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copyfile(source, dest)
    rel = os.path.relpath(os.path.dirname(dest), RACINE)
    res = {"dossier": rel, "onedrive": "non"}
    if onedrive_entree:
        try:
            await ha.appel("POST", "/services/onedrive/upload", {
                "config_entry_id": onedrive_entree, "filename": [dest],
                "destination_folder": f"{DOSSIER_ONEDRIVE}/{rel}"}, delai=300)
            res["onedrive"] = "ok"
        except Exception as e:  # la copie locale reste : on le dit, sans bloquer
            LOG.warning("OneDrive : %s", str(e)[:120])
            res["onedrive"] = "échec"
    LOG.info("Document classé (OneDrive %s)", res["onedrive"])
    return res
