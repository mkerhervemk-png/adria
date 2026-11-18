"""« Sors-moi le papier » (lot A6) : retrouver un document classé et l'envoyer sur le téléphone, 0 IA.

Cherche dans /media/adria_documents/<Personne>/<Type>/<date>_<titre> (classement A3) avec les mots de
la demande (« attestation assurance Inster », « dernière facture Free »). Tous les mots doivent être
dans personne + type + titre (sinon tous sauf un à partir de 3 mots). Le plus récent passe devant.
Rien trouvé = rien envoyé (jamais un papier au hasard).

Voix : événement `adria_core_papier` {question, req_id} -> notification avec aperçu et bouton
« Ouvrir » vers la page ADRIA, puis événement `adria_core_papier_resultat` {req_id, ok, texte}.
Page : champ « Retrouver un papier ». Actif si input_boolean.adria_core_papiers est ON.
"""

import logging
import os
import re
import subprocess

from store import mots_cles, normaliser

LOG = logging.getLogger("adria_core.retrouver")

RACINE = "/media/adria_documents"
INTERRUPTEUR = "input_boolean.adria_core_papiers"
NOM = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+?)(?:_\d{1,2})?$")
EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".webp", ".html")
# racines (5 lettres, comme mots_cles) des mots de la demande qui ne décrivent pas le papier
MOTS_DEMANDE = {"envoi", "sors", "sort", "trouv", "retro", "papie", "docum", "montr", "ouvre",
                "ouvri", "besoi", "voudr", "veux", "affic", "cherc", "telep", "porta", "copie",
                "recen", "nouve", "actue", "moi", "ici", "stp", "plait"}
MAX = 5


def mots_demande(question: str) -> list[str]:
    return [m for m in mots_cles(question) if m not in MOTS_DEMANDE]


def documents(racine: str | None = None) -> list[dict]:
    docs = []
    racine = racine or RACINE
    if not os.path.isdir(racine):
        return docs
    for personne in sorted(os.listdir(racine)):
        dp = os.path.join(racine, personne)
        if not os.path.isdir(dp):
            continue
        for typ in sorted(os.listdir(dp)):
            dt = os.path.join(dp, typ)
            if not os.path.isdir(dt):
                continue
            for f in os.listdir(dt):
                tige, ext = os.path.splitext(f)
                m = NOM.match(tige)
                if not m or ext.lower() not in EXTENSIONS:
                    continue
                docs.append({"chemin": f"{personne}/{typ}/{f}", "date": m.group(1), "personne": personne,
                             "type": typ, "titre": re.sub(r"[_\s]+", " ", m.group(2)).strip()})
    return docs


def chercher(question: str, racine: str | None = None, n: int = MAX) -> list[dict]:
    """Tous les mots d'abord ; s'il n'y a rien et au moins 3 mots, tous sauf un."""
    mots = mots_demande(question)
    if not mots:
        return []
    notes = []
    for d in documents(racine or RACINE):
        jetons = set(re.findall(r"[a-z0-9]+", normaliser(f"{d['personne']} {d['type']} {d['titre']}")))
        notes.append((sum(1 for m in mots if any(j.startswith(m) for j in jetons)), d["date"], d))
    for besoin in (len(mots), len(mots) - 1) if len(mots) >= 3 else (len(mots),):
        trouves = sorted((t for t in notes if t[0] >= besoin), key=lambda t: (t[0], t[1]), reverse=True)
        if trouves:
            return [d for _, _, d in trouves[:n]]
    return []


def chemin_sur(relatif: str, racine: str | None = None) -> str | None:
    """Chemin relatif reçu de la page -> chemin réel, seulement s'il reste dans la racine."""
    base = os.path.realpath(racine or RACINE)
    vrai = os.path.realpath(os.path.join(base, str(relatif or "")))
    if not vrai.startswith(base + os.sep) or not os.path.isfile(vrai):
        return None
    return vrai if vrai.lower().endswith(EXTENSIONS) else None


def libelle(d: dict) -> str:
    j = d["date"]
    return f"{d['titre']} ({d['personne']}, {d['type']}, {j[8:10]}/{j[5:7]}/{j[:4]})"


def apercu(source: str, sortie_png: str) -> bool:
    """1re page d'un PDF (ou l'image) en PNG pour la notification."""
    if source.lower().endswith(".html"):
        return False
    os.makedirs(os.path.dirname(sortie_png), exist_ok=True)
    try:
        if source.lower().endswith(".pdf"):
            tige = sortie_png[:-4]
            subprocess.run(["pdftoppm", "-r", "60", "-png", "-singlefile", "-f", "1", "-l", "1",
                            source, tige], check=True, timeout=60)
        else:
            with open(source, "rb") as a, open(sortie_png, "wb") as b:
                b.write(a.read())
        return os.path.isfile(sortie_png)
    except Exception as e:
        LOG.warning("Aperçu : %s", type(e).__name__)
        return False


class Retrouver:
    """`ha` = le Core (etat, appel). `formulaires` donne le notificateur et les dossiers de la page."""

    def __init__(self, ha, formulaires) -> None:
        self.ha = ha
        self.formulaires = formulaires
        self.dernier: dict | None = None  # dernier papier demandé à la voix (affiché en haut de la page)

    async def demander(self, data: dict) -> dict:
        import formulaires as F
        question = str(data.get("question") or "")
        res = {"req_id": data.get("req_id", ""), "ok": False, "texte": ""}
        if await self.ha.etat(INTERRUPTEUR) != "on":
            res["texte"] = "La recherche de papiers est éteinte."
            return res
        trouves = chercher(question)
        if not trouves:
            res["texte"] = "Je n'ai trouvé aucun papier classé qui corresponde. Je n'envoie rien."
            return res
        d = trouves[0]
        self.dernier = d
        png = os.path.join(F.DOSSIER, "papier", "apercu.png")
        image = f"{F.URL_LOCAL}/papier/apercu.png" if apercu(os.path.join(RACINE, d["chemin"]), png) else None
        autres = f"\n+ {len(trouves) - 1} autre(s) sur la page." if len(trouves) > 1 else ""
        domaine, service = self.formulaires.notifier.split(".", 1)
        notif = {"tag": "adria_papier", "channel": "ADRIA papiers", "importance": "high",
                 "clickAction": F.PAGE_UI,
                 "actions": [{"action": "URI", "title": "Ouvrir / télécharger", "uri": F.PAGE_UI}]}
        if image:
            notif["image"] = image
        await self.ha.appel("POST", f"/services/{domaine}/{service}",
                            {"title": "📄 Papier trouvé", "message": libelle(d) + autres, "data": notif})
        res.update(ok=True, texte=f"Je t'ai envoyé sur ton téléphone : {libelle(d)}.")
        LOG.info("Papier demandé : trouvé (%s résultat(s))", len(trouves))
        return res
