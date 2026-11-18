"""Agent Courriers (lot A1) : ADRIA lit un courrier et propose ses échéances pour l'agenda.

Fonctionnement (si input_boolean.adria_core_courriers est ON) :
1. Tu envoies un courrier (PDF ou photo) depuis la page « ADRIA Formulaires », mode Courrier.
2. Gemini Vision lit toutes les pages en un seul appel : expéditeur, objet, résumé, dates limites.
3. Notification : résumé en 2 lignes, puis pour chaque date « Ajouter à l'agenda ? »
   OUI / NON / Modifier la date. Rien n'est ajouté sans ton OUI.
4. OUI -> événement dans le calendrier ADRIA de Mael (expéditeur, action, objet, date).
5. Chaque jour à l'heure de rappel, les échéances à J-3 te sont rappelées par notification.

Confidentialité : le résumé reste en mémoire vive (ni base du Core, ni journaux, ni listes
ADRIA). Seuls expéditeur, action, objet et date vont dans l'agenda, après ton OUI.
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import uuid
from datetime import date, datetime, timedelta

import formulaires as F

LOG = logging.getLogger("adria_core.courriers")

SOUS_DOSSIER = "courriers"
MAX_PAGES = 3
MAX_ECHEANCES = 3
ATTENTE_REPONSE = 15 * 60
MARQUE = "Courrier lu par ADRIA"
TAG = "adria_courrier"
EXT_IMAGES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

PROMPT = (
    "Tu lis un courrier administratif ou personnel francais (photo ou scan, une ou plusieurs "
    "pages). Nous sommes le {jour}. Reponds UNIQUEMENT par un JSON : "
    '{{"expediteur": "organisme ou personne qui ecrit (court)", "objet": "sujet en quelques mots", '
    '"resume": "ce que dit le courrier et ce qu il faut faire, 2 phrases maximum", '
    '"echeances": [ {{"date": "AAAA-MM-JJ", "heure": "HH:MM ou vide", '
    '"action": "ce qu il faut faire a cette date, en 6 mots maximum"}} ]}}. '
    "Une echeance = une date limite, un rendez-vous, une convocation, un prelevement ou une "
    "date de renouvellement QUI CONCERNE LE DESTINATAIRE. Ignore la date d envoi du courrier, "
    "les dates passees et les dates de mentions legales. Si l annee n est pas ecrite, prends la "
    "prochaine occurrence. Aucune echeance -> liste vide. N invente rien. N ecris jamais de numero "
    "de compte, de carte, de securite sociale, de mot de passe ni de montant dans action ou objet."
)


# --------------------------------------------------------------------- outils purs (testés)

def _court(t, n: int) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _heure(t) -> str:
    m = re.fullmatch(r"\s*([01]?\d|2[0-3])[:hH]([0-5]\d)\s*", str(t or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def analyser(d: dict, aujourdhui: date) -> dict:
    """Réponse Gemini -> courrier propre. Dates invalides ou passées écartées, 3 max, triées."""
    echeances, vues = [], set()
    for e in (d.get("echeances") or []) if isinstance(d, dict) else []:
        if not isinstance(e, dict):
            continue
        try:
            jour = date.fromisoformat(str(e.get("date") or "").strip()[:10])
        except ValueError:
            continue
        if jour < aujourdhui or jour > aujourdhui + timedelta(days=730):
            continue
        h = _heure(e.get("heure"))
        cle = (jour, h)
        if cle in vues:
            continue
        vues.add(cle)
        echeances.append({"date": jour, "heure": h, "action": _court(e.get("action") or "À traiter", 50)})
    echeances.sort(key=lambda e: (e["date"], e["heure"]))
    d = d if isinstance(d, dict) else {}
    return {"expediteur": _court(d.get("expediteur") or "Courrier", 40),
            "objet": _court(d.get("objet") or "", 60),
            "resume": _court(d.get("resume") or "", 220),
            "echeances": echeances[:MAX_ECHEANCES]}


def lire_date(texte: str, aujourdhui: date):
    """« 25/11 », « 25/11/2026 », « 25-11-26 », « 25.11 14h30 » -> (date, 'HH:MM' ou '') ou None."""
    t = str(texte or "").strip()
    m = re.match(r"^(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2,4}))?(?:\s+(?:a|à)?\s*(\S+))?$", t)
    if not m:
        return None
    j, mo, a = int(m.group(1)), int(m.group(2)), m.group(3)
    try:
        if a:
            an = int(a) + (2000 if len(a) == 2 else 0)
            jour = date(an, mo, j)
        else:
            jour = date(aujourdhui.year, mo, j)
            if jour < aujourdhui:
                jour = date(aujourdhui.year + 1, mo, j)
    except ValueError:
        return None
    if jour < aujourdhui:
        return None
    h = _heure(m.group(4)) if m.group(4) else ""
    if m.group(4) and not h:
        return None
    return jour, h


def libelle_date(jour: date, heure: str = "") -> str:
    jours = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
    return f"{jours[jour.weekday()]} {jour.strftime('%d/%m/%Y')}" + (f" à {heure}" if heure else "")


def evenement(courrier: dict, e: dict, lu_le: date) -> dict:
    """Données du service calendar.create_event (sans entity_id)."""
    data = {"summary": _court(f"📬 {courrier['expediteur']} — {e['action']}", 90),
            "description": f"{MARQUE} le {lu_le.strftime('%d/%m/%Y')}."
                           + (f" Objet : {courrier['objet']}." if courrier.get("objet") else "")}
    if e.get("heure"):
        debut = datetime.combine(e["date"], datetime.strptime(e["heure"], "%H:%M").time())
        data["start_date_time"] = debut.strftime("%Y-%m-%d %H:%M:%S")
        data["end_date_time"] = (debut + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        data["start_date"] = e["date"].isoformat()
        data["end_date"] = (e["date"] + timedelta(days=1)).isoformat()
    return data


def rappels_du_jour(evenements: list[dict]) -> list[str]:
    """Événements du calendrier -> titres des échéances posées par ADRIA (marque dans la description)."""
    return [str(ev.get("summary") or "") for ev in evenements or []
            if str(ev.get("description") or "").startswith(MARQUE)]


def type_fichier(donnees: bytes) -> str | None:
    if donnees.startswith(b"%PDF"):
        return ".pdf"
    if donnees.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if donnees.startswith(b"\x89PNG"):
        return ".png"
    if donnees[:4] == b"RIFF" and donnees[8:12] == b"WEBP":
        return ".webp"
    return None


# --------------------------------------------------------------------------- l'agent

class AgentCourriers:
    """`ha` = le Core : etat(), appel(), ai_task_vision(). `formulaires` sert au partage du téléphone."""

    def __init__(self, ha, formulaires, notifier: str, calendrier: str = "calendar.mael_adria") -> None:
        self.ha = ha
        self.formulaires = formulaires
        self.notifier = notifier
        self.calendrier = calendrier
        self.en_cours: str | None = None
        self.attentes: dict[str, asyncio.Future] = {}
        self.dernier: dict | None = None

    @staticmethod
    def dossier(*parties: str) -> str:
        return os.path.join(F.DOSSIER, SOUS_DOSSIER, *parties)

    # ---- notifications et réponses
    async def notifier_tel(self, titre: str, message: str, actions=None) -> None:
        domaine, service = self.notifier.split(".", 1)
        data = {"tag": TAG, "sticky": "true", "channel": "ADRIA courriers", "importance": "high"}
        if actions:
            data["actions"] = actions
        await self.ha.appel("POST", f"/services/{domaine}/{service}",
                            {"title": titre, "message": message, "data": data})

    def recevoir_action(self, data: dict) -> None:
        """Événement mobile_app_notification_action (rien n'est journalisé)."""
        action = str(data.get("action") or "")
        if action == "REPLY":
            fut = self.attentes.get("REPLY")
            tag = data.get("tag")
            if fut and not fut.done() and (tag == TAG or (not tag and "REPLY" not in self.formulaires.attentes)):
                fut.set_result(str(data.get("reply_text") or "").strip())
            return
        if action.startswith("ADRIA_COUR_"):
            cle, _, choix = action.rpartition("_")
            fut = self.attentes.get(cle)
            if fut and not fut.done():
                fut.set_result(choix)

    async def demander(self, titre: str, message: str, boutons: list[tuple[str, str]] | None) -> str | None:
        """boutons = [(CODE, libellé)] ; None = réponse texte (Répondre)."""
        cle = "REPLY" if boutons is None else f"ADRIA_COUR_{uuid.uuid4().hex[:8]}"
        fut = asyncio.get_running_loop().create_future()
        self.attentes[cle] = fut
        actions = ([{"action": "REPLY", "title": "Répondre"}] if boutons is None else
                   [{"action": f"{cle}_{code}", "title": lib} for code, lib in boutons])
        try:
            await self.notifier_tel(titre, message, actions)
            return await asyncio.wait_for(fut, ATTENTE_REPONSE)
        except asyncio.TimeoutError:
            return None
        finally:
            self.attentes.pop(cle, None)

    # ---- lecture
    async def lire(self, travail: str, fichier: str, ident: str) -> dict:
        ext = os.path.splitext(fichier)[1].lower()
        if ext == ".pdf":
            subprocess.run(["pdftoppm", "-r", "110", "-png", "-l", str(MAX_PAGES), fichier,
                            os.path.join(travail, "p")], check=True, timeout=120)
            images = sorted(f for f in os.listdir(travail) if re.fullmatch(r"p-\d+\.png", f))
            medias = [(f, "image/png") for f in images]
        else:
            medias = [(os.path.basename(fichier), EXT_IMAGES[ext])]
        if not medias:
            raise RuntimeError("aucune page lisible")
        rep = await self.ha.ai_task_vision(
            PROMPT.format(jour=date.today().strftime("%d/%m/%Y")), "adria_core_courrier",
            [{"media_content_id": f"{F.MEDIA_LOCAL}/{SOUS_DOSSIER}/travail/{ident}/{nom}",
              "media_content_type": typ} for nom, typ in medias])
        return F.lire_json(rep)

    async def ajouter(self, courrier: dict, e: dict) -> None:
        data = evenement(courrier, e, date.today())
        data["entity_id"] = self.calendrier
        await self.ha.appel("POST", "/services/calendar/create_event", data)

    async def proposer(self, courrier: dict) -> tuple[int, int]:
        """Une question par échéance. Retourne (ajoutées, proposées)."""
        ajoutees, total = 0, len(courrier["echeances"])
        titre = f"📬 {courrier['expediteur']}"
        for n, e in enumerate(courrier["echeances"], 1):
            modifs = 0
            while True:
                boutons = [("OUI", "OUI"), ("NON", "NON")]
                if modifs < 2:  # 2 corrections de date au plus
                    boutons.append(("MODIF", "Modifier la date"))
                r = await self.demander(
                    f"{titre} — {n}/{total}",
                    f"{e['action']} : {libelle_date(e['date'], e['heure'])}. Ajouter à l'agenda ?",
                    boutons)
                if r != "MODIF":
                    break
                modifs += 1
                texte = await self.demander(f"{titre} — nouvelle date",
                                            "Écris la date : jj/mm, jj/mm/aaaa, ou jj/mm 14h30.", None)
                if texte is None:
                    r = None
                    break
                nouvelle = lire_date(texte, date.today())
                if nouvelle is None:
                    await self.notifier_tel(titre, "Date non comprise : je garde l'ancienne.")
                else:
                    e["date"], e["heure"] = nouvelle
            if r is None:
                await self.notifier_tel(titre, "Pas de réponse en 15 min : rien n'est ajouté à l'agenda.")
                break
            if r == "OUI":
                await self.ajouter(courrier, e)
                ajoutees += 1
        return ajoutees, total

    # ---- un courrier de bout en bout
    async def traiter(self, chemin: str) -> dict:
        maintenant = datetime.now()
        ident = maintenant.strftime("%Y%m%d%H%M%S")
        travail = self.dossier("travail", ident)
        os.makedirs(travail, exist_ok=True)
        fichier = os.path.join(travail, "source" + os.path.splitext(chemin)[1].lower())
        shutil.copyfile(chemin, fichier)
        res = {"fichier": os.path.basename(chemin), "debut": maintenant.isoformat(timespec="seconds"),
               "ok": False}
        self.en_cours = res["fichier"]
        courrier = None
        try:
            courrier = analyser(await self.lire(travail, fichier, ident), maintenant.date())
            res["echeances"] = len(courrier["echeances"])
            lignes = [courrier["objet"], courrier["resume"]]
            if not courrier["echeances"]:
                lignes.append("Aucune date limite trouvée : rien à mettre dans l'agenda.")
            await self.notifier_tel(f"📬 {courrier['expediteur']}", "\n".join(l for l in lignes if l))
            res["ajoutees"] = (await self.proposer(courrier))[0] if courrier["echeances"] else 0
            if res["ajoutees"]:
                await self.notifier_tel(f"✅ {courrier['expediteur']}",
                                        f"{res['ajoutees']} échéance(s) dans l'agenda, rappel 3 jours avant.")
            res["ok"] = True
        except Exception as e:
            LOG.warning("Courrier %s : %s", res["fichier"], type(e).__name__)
            res["erreur"] = str(e)[:160]
            try:
                await self.notifier_tel("⚠️ Courrier non lu", f"{res['fichier']} : {res['erreur']}.")
            except Exception:
                pass
        finally:
            courrier = None  # le résumé ne reste pas en mémoire
            self.en_cours = None
        faits = self.dossier("entree", "traites" if res["ok"] else "echecs")
        os.makedirs(faits, exist_ok=True)
        try:
            shutil.move(chemin, os.path.join(faits, f"{ident}_{os.path.basename(chemin)}"))
        except OSError:
            pass
        res["fin"] = datetime.now().isoformat(timespec="seconds")
        LOG.info("Courrier : %s", {k: v for k, v in res.items() if k != "erreur"})
        self.dernier = res
        return res

    # ---- rappel J-3 (relit l'agenda : rien à retenir, survit aux redémarrages)
    async def rappeler(self, aujourdhui: date | None = None) -> int:
        if await self.ha.etat("input_boolean.adria_core_courriers") != "on":
            return 0
        cible = (aujourdhui or date.today()) + timedelta(days=3)
        rep = await self.ha.appel("POST", "/services/calendar/get_events?return_response", {
            "entity_id": self.calendrier,
            "start_date_time": f"{cible.isoformat()} 00:00:00",
            "end_date_time": f"{(cible + timedelta(days=1)).isoformat()} 00:00:00"})
        evs = ((rep.get("service_response") or {}).get(self.calendrier) or {}).get("events") or []
        titres = rappels_du_jour(evs)
        if titres:
            await self.notifier_tel(f"⏰ Dans 3 jours ({libelle_date(cible)})",
                                    "\n".join(t.removeprefix("📬 ") for t in titres))
        return len(titres)

    # ---- boucle : surveille le dossier courriers/entree
    async def surveiller(self) -> None:
        entree = self.dossier("entree")
        while True:
            try:
                os.makedirs(entree, exist_ok=True)
                libre = self.en_cours is None and self.formulaires.en_cours is None
                if libre and await self.ha.etat("input_boolean.adria_core_courriers") == "on":
                    fichiers = sorted(f for f in os.listdir(entree)
                                      if os.path.splitext(f)[1].lower() in (".pdf", *EXT_IMAGES))
                    if fichiers:
                        await self.traiter(os.path.join(entree, fichiers[0]))
                        await self.ha.publier_etat_formulaires()
            except Exception:
                LOG.exception("Surveillance courriers")
            await asyncio.sleep(30)
