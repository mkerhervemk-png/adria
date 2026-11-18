"""Agent Formulaires (lot F1) : ADRIA remplit un PDF à ta place, sans rien retenir de sensible.

Fonctionnement (si input_boolean.adria_core_formulaires est ON) :
1. Tu déposes un PDF dans Médias > adria_formulaires > entree (appli HA, téléphone ou PC).
2. Le Core dessine chaque page en image, Gemini Vision repère les champs à remplir.
3. Ce qui est sûr vient du profil de l'appli (nom, prénom, naissance, téléphone, adresse…)
   et la date du jour. Tout le reste (santé, n° de sécurité sociale, etc.) t'est demandé
   par notification, une question à la fois. Les réponses restent en mémoire vive le temps
   du remplissage : jamais écrites dans la base du Core, jamais dans les journaux.
4. Le PDF rempli (sans signature) + un aperçu arrivent dans adria_formulaires/sortie, et
   une notification te le montre. ADRIA n'envoie jamais le document à personne.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import uuid
from datetime import date, datetime
from html import unescape

LOG = logging.getLogger("adria_core.formulaires")

DOSSIER = "/media/adria_formulaires"
MEDIA_LOCAL = "media-source://media_source/local/adria_formulaires"
URL_LOCAL = "/media/local/adria_formulaires"
DOSSIER_UI = ("/media-browser/browser/app%2Cmedia-source%3A%2F%2Fmedia_source"
              "/media-source%3A%2F%2Fmedia_source%2Flocal%2Fadria_formulaires%2Fsortie")
MAX_PAGES = 4
ATTENTE_REPONSE = 15 * 60  # s par question
CLES_PROFIL = ("nom", "prenom", "naissance", "lieu_naissance", "adresse", "code_postal",
               "ville", "telephone", "email")
LEADER = re.compile(r"^[.…_\-]{3,}$")

PROMPT = (
    "Tu regardes une page de formulaire administratif francais (image). Repere chaque champ "
    "que la personne doit remplir. Reponds UNIQUEMENT par un JSON : "
    '{"titre": "nom court du document", "champs": [ {"libelle": "texte du champ", '
    '"type": "texte|oui_non|case|signature", "cle": "nom|prenom|nom_prenom|naissance|'
    'lieu_naissance|adresse|code_postal|ville|telephone|email|date_jour|lieu_signature|autre", '
    '"question": "question courte et claire a poser a la personne", "deja_rempli": false, '
    '"zone": [ymin, xmin, ymax, xmax], "zone_oui": [ymin, xmin, ymax, xmax], '
    '"zone_non": [ymin, xmin, ymax, xmax]} ]}. '
    "Coordonnees entieres de 0 a 1000 relatives a l image. zone = l espace VIDE ou ecrire la "
    "reponse (pointilles, ligne, case vide, a droite du libelle), jamais le libelle lui-meme. "
    "type oui_non = une question avec une case OUI et une case NON : donne zone_oui et zone_non "
    "(les cases de la ligne de la question). type case = une case a cocher seule. "
    "deja_rempli = true si le champ contient deja une reponse. Fait le / Date = cle date_jour ; "
    "A (ville de signature) = lieu_signature ; nom et prenom dans un seul champ = nom_prenom. "
    "N oublie aucun champ, n invente aucun champ, ignore les zones reservees a l administration."
)


# --------------------------------------------------------------------- outils purs (testés)

def lire_json(texte: str) -> dict:
    """Extrait le premier objet JSON d'une réponse d'IA (avec ou sans ```json)."""
    if isinstance(texte, dict):
        return texte
    t = (texte or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    debut, fin = t.find("{"), t.rfind("}")
    if debut < 0 or fin <= debut:
        return {}
    try:
        return json.loads(t[debut:fin + 1])
    except ValueError:
        return {}


def mots_pdf(xml: str) -> list[dict]:
    """Analyse `pdftotext -bbox-layout` : [{page, texte, x0, y0, x1, y1, w, h}] en points."""
    mots, page, w, h = [], 0, 0.0, 0.0
    for m in re.finditer(r'<page width="([\d.]+)" height="([\d.]+)"|<word xMin="([\d.]+)" '
                         r'yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', xml):
        if m.group(1):
            page += 1
            w, h = float(m.group(1)), float(m.group(2))
        else:
            mots.append({"page": page, "texte": unescape(m.group(7)), "x0": float(m.group(3)),
                         "y0": float(m.group(4)), "x1": float(m.group(5)), "y1": float(m.group(6)),
                         "w": w, "h": h})
    return mots


def vers_points(zone, largeur: float, hauteur: float):
    """[ymin, xmin, ymax, xmax] 0-1000 -> (x0, y0, x1, y1) en points, origine en haut."""
    try:
        ymin, xmin, ymax, xmax = (float(v) for v in zone)
    except (TypeError, ValueError):
        return None
    if not (0 <= xmin < xmax <= 1000 and 0 <= ymin < ymax <= 1000):
        return None
    return (xmin * largeur / 1000, ymin * hauteur / 1000, xmax * largeur / 1000, ymax * hauteur / 1000)


def _meme_ligne(a0, a1, b0, b1, marge=3.0) -> bool:
    return min(a1, b1) - max(a0, b0) > -marge


def caler_texte(boite, mots: list[dict]) -> dict:
    """Où écrire dans la zone : sur les pointillés s'il y en a, sinon après le libellé."""
    x0, y0, x1, y1 = boite
    lignes = [m for m in mots if LEADER.match(m["texte"]) and _meme_ligne(m["y0"], m["y1"], y0, y1)
              and m["x1"] > x0 - 5 and m["x0"] < x1 + 5]
    if lignes:
        g = min(lignes, key=lambda m: m["x0"])
        d = max(m["x1"] for m in lignes if abs(m["y0"] - g["y0"]) < 3)
        return {"x": g["x0"] + 1, "base": g["y1"] - 1.5, "largeur": max(d - g["x0"], 30),
                "masque": (g["x0"] - 1, g["y0"] - 1, d + 1, g["y1"] + 1)}
    avant = [m for m in mots if m["x1"] <= x0 + 12 and m["x1"] > x0 - 120
             and _meme_ligne(m["y0"], m["y1"], y0, y1, 1) and not LEADER.match(m["texte"])]
    if avant:
        lib = max(avant, key=lambda m: m["x1"])
        return {"x": max(x0, lib["x1"] + 4), "base": lib["y1"] - 1.5,
                "largeur": max(x1 - max(x0, lib["x1"] + 4), 30), "masque": None}
    return {"x": x0 + 2, "base": min(y1 - 2, y0 + 12), "largeur": max(x1 - x0 - 2, 30), "masque": None}


def centre_case(boite, mots: list[dict], entete: str | None = None):
    """Centre d'une case. Si la page a une colonne OUI/NON, x = centre de l'en-tête."""
    x0, y0, x1, y1 = boite
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if entete:
        tetes = [m for m in mots if m["texte"].strip(" :").upper() == entete and m["y1"] <= cy + 2]
        if tetes:
            t = min(tetes, key=lambda m: (abs((m["x0"] + m["x1"]) / 2 - cx), cy - m["y1"]))
            if abs((t["x0"] + t["x1"]) / 2 - cx) < 40:
                cx = (t["x0"] + t["x1"]) / 2
    return cx, cy


def valeur_profil(cle: str, profil: dict, jour: date) -> str | None:
    p = {k: str(profil.get(k) or "").strip() for k in CLES_PROFIL}
    if cle == "date_jour":
        return jour.strftime("%d/%m/%Y")
    if cle == "nom_prenom":
        return " ".join(v for v in (p["nom"].upper(), p["prenom"]) if v) or None
    if cle == "nom":
        return p["nom"].upper() or None
    if cle == "lieu_signature":
        return p["ville"] or None
    return p.get(cle) or None


def normaliser(champs_bruts: list, page: int, mots: list[dict], largeur: float, hauteur: float) -> list[dict]:
    """Champs Gemini -> champs placés en points (page commence à 1)."""
    sortie = []
    mp = [m for m in mots if m["page"] == page]
    for c in champs_bruts or []:
        if not isinstance(c, dict) or c.get("deja_rempli") is True:
            continue
        typ = str(c.get("type") or "texte").lower()
        if typ == "signature":
            continue
        champ = {"page": page, "type": typ, "cle": str(c.get("cle") or "autre").lower(),
                 "libelle": str(c.get("libelle") or "").strip()[:120],
                 "question": str(c.get("question") or c.get("libelle") or "").strip()[:200]}
        if typ == "oui_non":
            bo = vers_points(c.get("zone_oui"), largeur, hauteur)
            bn = vers_points(c.get("zone_non"), largeur, hauteur)
            if not bo or not bn:
                continue
            champ["oui"] = centre_case(bo, mp, "OUI")
            champ["non"] = centre_case(bn, mp, "NON")
        else:
            b = vers_points(c.get("zone"), largeur, hauteur)
            if not b:
                continue
            if typ == "case":
                champ["centre"] = centre_case(b, mp)
            else:
                champ.update(caler_texte(b, mp))
        sortie.append(champ)
    return sortie


def dessiner(pdf_entree: str, pdf_sortie: str, champs: list[dict]) -> int:
    """Écrit les réponses (champ['valeur']) sur le PDF. Retourne le nombre de champs écrits."""
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    lecteur = PdfReader(pdf_entree)
    ecrivain = PdfWriter()
    ecrits = 0
    for i, source in enumerate(lecteur.pages, 1):
        page = ecrivain.add_page(source)
        a_ecrire = [c for c in champs if c["page"] == i and c.get("valeur") not in (None, "")]
        if a_ecrire:
            boite = page.mediabox
            larg, haut = float(boite.width), float(boite.height)
            ox, oy = float(boite.left), float(boite.bottom)
            tampon = BytesIO()
            cv = canvas.Canvas(tampon, pagesize=(larg, haut))
            for c in a_ecrire:
                v = c["valeur"]
                if c["type"] in ("oui_non", "case"):
                    if c["type"] == "oui_non":
                        cx, cy = c["oui"] if v == "oui" else c["non"]
                    elif v == "oui":
                        cx, cy = c["centre"]
                    else:
                        continue
                    cv.setFont("Helvetica-Bold", 12)
                    cv.drawCentredString(cx - ox, haut - cy - 4, "X")
                else:
                    if c.get("masque"):
                        mx0, my0, mx1, my1 = c["masque"]
                        cv.setFillColorRGB(1, 1, 1)
                        cv.rect(mx0 - ox, haut - my1, mx1 - mx0, my1 - my0, fill=1, stroke=0)
                        cv.setFillColorRGB(0, 0, 0)
                    taille = 11.0
                    while taille > 7 and stringWidth(str(v), "Helvetica", taille) > c["largeur"]:
                        taille -= 0.5
                    cv.setFont("Helvetica", taille)
                    cv.drawString(c["x"] - ox, haut - c["base"], str(v))
                ecrits += 1
            cv.save()
            tampon.seek(0)
            page.merge_page(PdfReader(tampon).pages[0])
    with open(pdf_sortie, "wb") as f:
        ecrivain.write(f)
    return ecrits


def nom_sortie(nom: str, maintenant: datetime) -> str:
    base = re.sub(r"[^\w\-]+", "_", os.path.splitext(os.path.basename(nom))[0]).strip("_")[:60] or "formulaire"
    return f"{base}_rempli_{maintenant.strftime('%Y%m%d_%H%M')}"


# --------------------------------------------------------------------------- l'agent

class AgentFormulaires:
    """`ha` = le Core : etat(), appel(), ai_task_vision(), incrementer()."""

    def __init__(self, ha, profil: dict | None = None,
                 notifier: str = "notify.mobile_app_telephone_mael_sm_s921b") -> None:
        self.ha = ha
        self.profil = profil or {}
        self.notifier = notifier
        self.en_cours: str | None = None
        self.attentes: dict[str, asyncio.Future] = {}
        self.dernier: dict | None = None

    # ---- notifications et réponses
    async def notifier_tel(self, titre: str, message: str, actions=None, tag="adria_formulaire",
                           image: str | None = None) -> None:
        domaine, service = self.notifier.split(".", 1)
        data = {"tag": tag, "sticky": "true", "channel": "ADRIA formulaires", "importance": "high"}
        if actions:
            data["actions"] = actions
        if image:
            data["image"] = image
        await self.ha.appel("POST", f"/services/{domaine}/{service}",
                            {"title": titre, "message": message, "data": data})

    def recevoir_action(self, data: dict) -> None:
        """Événement mobile_app_notification_action (aucune réponse n'est journalisée)."""
        action = str(data.get("action") or "")
        if action == "REPLY":
            fut = self.attentes.get("REPLY")
            if fut and not fut.done() and (not data.get("tag") or data.get("tag") == "adria_formulaire"):
                fut.set_result(str(data.get("reply_text") or "").strip())
            return
        if action.startswith("ADRIA_FORM_"):
            cle, _, choix = action.rpartition("_")
            fut = self.attentes.get(cle)
            if fut and not fut.done():
                fut.set_result(choix)

    async def demander(self, n: int, total: int, titre_doc: str, champ: dict, choix: bool,
                       question: str | None = None) -> str | None:
        cle = f"ADRIA_FORM_{uuid.uuid4().hex[:8]}"
        q = question or champ["question"]
        fut = asyncio.get_running_loop().create_future()
        if choix:
            self.attentes[cle] = fut
            boutons = ([{"action": f"{cle}_OUI", "title": "OUI"}, {"action": f"{cle}_NON", "title": "NON"}]
                       if champ.get("type") != "groupe" else
                       [{"action": f"{cle}_TOUTNON", "title": "Tout NON"},
                        {"action": f"{cle}_UNEPARUNE", "title": "Une par une"}])
            boutons.append({"action": f"{cle}_PASSER", "title": "Passer"})
        else:
            self.attentes["REPLY"] = fut
            boutons = [{"action": "REPLY", "title": "Répondre"}]
        try:
            await self.notifier_tel(f"📝 {titre_doc} — {n}/{total}", q, boutons)
            return await asyncio.wait_for(fut, ATTENTE_REPONSE)
        except asyncio.TimeoutError:
            return None
        finally:
            self.attentes.pop(cle if choix else "REPLY", None)

    # ---- détection
    async def detecter(self, travail: str, pdf: str, nom_media: str) -> tuple[str, list[dict]]:
        subprocess.run(["pdftoppm", "-r", "110", "-png", "-l", str(MAX_PAGES), pdf,
                        os.path.join(travail, "p")], check=True, timeout=120)
        xml = subprocess.run(["pdftotext", "-bbox-layout", "-l", str(MAX_PAGES), pdf, "-"],
                             capture_output=True, text=True, timeout=60).stdout
        mots = mots_pdf(xml)
        from pypdf import PdfReader
        pages = PdfReader(pdf).pages[:MAX_PAGES]
        titre, champs = "", []
        images = sorted(f for f in os.listdir(travail) if re.fullmatch(r"p-\d+\.png", f))
        for i, (img, page) in enumerate(zip(images, pages), 1):
            rep = await self.ha.ai_task_vision(
                PROMPT, "adria_core_formulaire",
                [{"media_content_id": f"{MEDIA_LOCAL}/travail/{nom_media}/{img}",
                  "media_content_type": "image/png"}])
            d = lire_json(rep)
            titre = titre or str(d.get("titre") or "")
            champs += normaliser(d.get("champs") or [], i, mots,
                                 float(page.mediabox.width), float(page.mediabox.height))
        return titre[:60] or "Formulaire", champs

    # ---- un formulaire de bout en bout
    async def traiter(self, chemin: str) -> dict:
        maintenant = datetime.now()
        ident = maintenant.strftime("%Y%m%d%H%M%S")
        travail = os.path.join(DOSSIER, "travail", ident)
        os.makedirs(travail, exist_ok=True)
        os.makedirs(os.path.join(DOSSIER, "sortie"), exist_ok=True)
        pdf = os.path.join(travail, "source.pdf")
        shutil.copyfile(chemin, pdf)
        res = {"fichier": os.path.basename(chemin), "debut": maintenant.isoformat(timespec="seconds"),
               "ok": False}
        self.en_cours = res["fichier"]
        try:
            titre, champs = await self.detecter(travail, pdf, ident)
            res["champs"] = len(champs)
            if not champs:
                raise RuntimeError("aucun champ reconnu")
            for c in champs:
                c["valeur"] = valeur_profil(c["cle"], self.profil, maintenant.date()) if c["type"] == "texte" else None
            res["auto"] = sum(1 for c in champs if c.get("valeur"))
            res["questions"] = await self.poser_questions(titre, [c for c in champs if not c.get("valeur")])
            sortie = nom_sortie(chemin, maintenant)
            pdf_sortie = os.path.join(DOSSIER, "sortie", sortie + ".pdf")
            res["ecrits"] = dessiner(pdf, pdf_sortie, champs)
            subprocess.run(["pdftoppm", "-r", "80", "-png", "-singlefile", "-f", "1", "-l", "1",
                            pdf_sortie, os.path.join(DOSSIER, "sortie", sortie)], check=True, timeout=60)
            vides = [c["libelle"] for c in champs if c.get("valeur") in (None, "")]
            res.update(ok=True, sortie=f"sortie/{sortie}.pdf", vides=len(vides))
            await self.notifier_tel(
                f"✅ {titre} rempli",
                f"{res['ecrits']} champs remplis"
                + (f", {len(vides)} laissés vides ({', '.join(vides[:3])})" if vides else "")
                + ". Signature à faire par toi. Médias > adria_formulaires > sortie > " + sortie + ".pdf",
                [{"action": "URI", "title": "Voir le dossier", "uri": DOSSIER_UI}],
                image=f"{URL_LOCAL}/sortie/{sortie}.png")
        except Exception as e:
            LOG.warning("Formulaire %s : %s", res["fichier"], type(e).__name__)
            res["erreur"] = str(e)[:160]
            try:
                await self.notifier_tel("⚠️ Formulaire non rempli",
                                        f"{res['fichier']} : {res['erreur']}. Le fichier reste dans entree.")
            except Exception:
                pass
        finally:
            for c in locals().get("champs") or []:
                c["valeur"] = None  # rien de sensible ne reste en mémoire
            self.en_cours = None
        # rangé (jamais effacé) : traites si OK, echecs sinon, pour ne pas reboucler
        faits = os.path.join(DOSSIER, "entree", "traites" if res["ok"] else "echecs")
        os.makedirs(faits, exist_ok=True)
        try:
            shutil.move(chemin, os.path.join(faits, f"{ident}_{os.path.basename(chemin)}"))
        except OSError:
            pass
        res["fin"] = datetime.now().isoformat(timespec="seconds")
        LOG.info("Formulaire : %s", {k: v for k, v in res.items() if k != "erreur"})
        self.dernier = res
        return res

    async def poser_questions(self, titre: str, champs: list[dict]) -> int:
        oui_non = [c for c in champs if c["type"] in ("oui_non", "case")]
        textes = [c for c in champs if c["type"] == "texte"]
        total = len(textes) + len(oui_non)
        n, posees = 0, 0
        if len(oui_non) >= 2:
            resume = " ; ".join(c["libelle"][:45] for c in oui_non[:8])
            r = await self.demander(1, total, titre, {"type": "groupe"}, True,
                                    f"{len(oui_non)} questions oui/non : {resume}. Tout NON ?")
            posees += 1
            if r == "TOUTNON":
                for c in oui_non:
                    c["valeur"] = "non" if c["type"] == "oui_non" else None
                oui_non = []
            elif r is None or r == "PASSER":
                oui_non = []
        for c in oui_non + textes:
            n += 1
            r = await self.demander(n, total, titre, c, c["type"] != "texte")
            posees += 1
            if r is None:
                await self.notifier_tel(f"⏸️ {titre}", "Pas de réponse en 15 min : je termine avec les cases vides.")
                break
            if c["type"] == "texte":
                c["valeur"] = r[:120] if r and r.lower() not in ("passer", "-") else None
            elif r in ("OUI", "NON"):
                c["valeur"] = r.lower()
        return posees

    # ---- boucle : surveille le dossier entree
    async def surveiller(self) -> None:
        entree = os.path.join(DOSSIER, "entree")
        while True:
            try:
                os.makedirs(entree, exist_ok=True)
                if self.en_cours is None and await self.ha.etat("input_boolean.adria_core_formulaires") == "on":
                    pdfs = sorted(f for f in os.listdir(entree) if f.lower().endswith(".pdf"))
                    if pdfs:
                        await self.traiter(os.path.join(entree, pdfs[0]))
                        await self.ha.publier_etat_formulaires()
            except Exception:
                LOG.exception("Surveillance formulaires")
            await asyncio.sleep(30)
