"""Registre d'ancrage du journal du Guardian (v0.22.1, révision 6).

BUT : prouver qu'aucune ligne du journal `guardian_journal` (base SQLite `/data/adria_core.db`) n'a disparu
à la fin, à partir d'une date connue. Protège contre les pertes, restaurations anciennes et troncatures
ACCIDENTELLES de la base. Ce n'est PAS un mécanisme inviolable : le même conteneur écrit la base et le
registre ; quelqu'un qui contrôle le conteneur ou Home Assistant peut réécrire les deux.

SUPPORT : fichier en ajout seul `/media/adria_core_ancres/guardian.jsonl`, hors de `/data` (même disque
physique sous Home Assistant OS, mais autre dossier, autre fichier, autre sauvegarde : une sauvegarde partielle
de l'appli ne contient que `/data`). Aucune dépendance au réseau ni à Home Assistant.

UNE LIGNE = un objet JSON terminé par « \\n » :
  {"v": 1, "seq": k, "type": "ligne" | "reference", "jid": id journal, "jhash": hash de cette ligne du journal,
   "n": nombre de lignes du journal à cet instant, "quand": horodatage, "origine": "...", "source": "...",
   ["observe": {...}, "note": "..."]  (reference seulement), "prec": h de la ligne précédente, "h": empreinte}
- seq commence à 1 et augmente de 1 (doublon, trou ou recul = divergence) ;
- prec/h forment la chaîne propre du registre ; h = SHA-256(prec + JSON canonique sans « h ») ;
- « ligne » ancre une écriture du journal juste après son commit ; « reference » (migration non vérifiée)
  déclare un point de départ : la complétude AVANT lui n'est pas démontrée.

ÉCRITURE : verrou exclusif `fcntl.flock` (fichier `.verrou`) pris AVANT la transaction SQLite et rendu APRÈS
l'ajout au registre : les écrivains (Core, procédure manuelle) s'alignent dans le même ordre des deux côtés.
Ajout = O_APPEND + fsync (+ fsync du dossier à la création). Un fichier qui ne finit pas par « \\n » (ligne
partielle) n'est JAMAIS complété ni réparé : l'ajout échoue, l'état devient dégradé.

Le registre n'est JAMAIS reconstruit à partir du journal, ni réparé, ni tronqué par le code.
"""

import contextlib
import errno
import fcntl
import hashlib
import json
import os
from datetime import datetime

DOSSIER = "/media/adria_core_ancres"
FICHIER = "guardian.jsonl"
CLES = {"v", "seq", "type", "jid", "jhash", "n", "quand", "origine", "source", "prec", "h"}
CLES_REFERENCE = CLES | {"observe", "note"}
ZERO = "0" * 64

# États rendus par verifier()
COMPLET = "complet"                          # ancré depuis la 1re ligne du journal
COMPLET_REF = "complet_depuis_reference"     # ancré sans trou depuis une référence non vérifiée
VERIFIES = (COMPLET, COMPLET_REF)
# Absence de preuve (une migration NON vérifiée explicite peut être envisagée) :
# (LACUNE : une ligne du journal n'a pas été ancrée — échec d'ancrage passé ; la chaîne du registre étant
#  intacte, ce trou ne peut pas venir d'une ligne retirée du registre, qui casserait sa chaîne.)
ABSENT, VIDE, PARTIEL, EN_RETARD, LACUNE = "absent", "vide", "partiel", "en_retard", "lacune"
SANS_PREUVE = (ABSENT, VIDE, PARTIEL, EN_RETARD, LACUNE)
# Preuve d'une incohérence (tout est refusé, rien n'est réparé) :
INACCESSIBLE, CORROMPU, TRONQUE, DIVERGENT = "inaccessible", "corrompu", "tronque", "divergent"


class RegistreErreur(Exception):
    pass


def empreinte(prec: str, entree: dict) -> str:
    sans_h = {k: v for k, v in entree.items() if k != "h"}
    return hashlib.sha256((prec + json.dumps(sans_h, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()


def _entier(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class Registre:
    def __init__(self, dossier: str = DOSSIER):
        self.dossier = dossier
        self.chemin = os.path.join(dossier, FICHIER)
        self.verrou_chemin = os.path.join(dossier, ".verrou")
        self.dernier_echec: str | None = None

    # ------------------------------------------------------------------ verrou
    @contextlib.contextmanager
    def verrou(self):
        """Verrou exclusif inter-processus. Rend True s'il est tenu, False si le dossier est indisponible
        (l'appelant continue SANS ancrer : état dégradé, jamais de blocage du Guardian)."""
        fd = None
        try:
            os.makedirs(self.dossier, exist_ok=True)
            fd = os.open(self.verrou_chemin, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as e:
            self.dernier_echec = f"verrou indisponible ({errno.errorcode.get(e.errno, type(e).__name__)})"
            if fd is not None:
                os.close(fd)
            fd = None
        try:
            yield fd is not None
        finally:
            if fd is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def sonde(self) -> dict:
        """Démarrage du Core : le dossier existe-t-il et peut-on y prendre le verrou ? (n'écrit pas d'entrée)"""
        with self.verrou() as tenu:
            return {"dossier": self.dossier, "accessible": tenu,
                    "fichier": os.path.exists(self.chemin), "echec": None if tenu else self.dernier_echec}

    # ------------------------------------------------------------------ écriture
    def _derniere(self) -> tuple[int, str]:
        """(seq, h) de la dernière entrée, en ne lisant que la fin du fichier. Lève RegistreErreur si la fin
        est partielle ou illisible (aucune réparation)."""
        if not os.path.exists(self.chemin) or os.path.getsize(self.chemin) == 0:
            return 0, ZERO
        with open(self.chemin, "rb") as f:
            taille = f.seek(0, os.SEEK_END)
            f.seek(max(0, taille - 65536))
            fin = f.read()
        if not fin.endswith(b"\n"):
            raise RegistreErreur("ligne partielle à la fin du registre")
        derniere = fin.rstrip(b"\n").rsplit(b"\n", 1)[-1]
        try:
            e = json.loads(derniere)
            return int(e["seq"]), str(e["h"])
        except (ValueError, KeyError, TypeError) as ex:
            raise RegistreErreur(f"dernière entrée illisible ({type(ex).__name__})") from None

    def ajouter(self, type_: str, jid: int, jhash: str, n: int, origine: str, source: str,
                quand: datetime | None = None, observe: dict | None = None, note: str | None = None) -> dict:
        """Ajoute UNE entrée (le verrou doit être tenu par l'appelant). Lève une exception en cas d'échec."""
        seq, prec = self._derniere()
        e = {"v": 1, "seq": seq + 1, "type": type_, "jid": int(jid), "jhash": str(jhash), "n": int(n),
             "quand": (quand or datetime.now()).isoformat(timespec="seconds"), "origine": origine,
             "source": source, "prec": prec}
        if type_ == "reference":
            e["observe"], e["note"] = observe or {}, note or ""
        e["h"] = empreinte(prec, e)
        nouveau = not os.path.exists(self.chemin)
        fd = os.open(self.chemin, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            donnees = (json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n").encode()
            if os.write(fd, donnees) != len(donnees):
                raise RegistreErreur("écriture incomplète du registre")
            os.fsync(fd)
        finally:
            os.close(fd)
        if nouveau:
            dfd = os.open(self.dossier, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        self.dernier_echec = None
        return e

    # ------------------------------------------------------------------ lecture et vérification
    def lire(self) -> list[dict]:
        """Toutes les entrées, vérifiées une à une. Lève RegistreErreur à la première anomalie."""
        with open(self.chemin, "rb") as f:
            brut = f.read()
        if not brut:
            return []
        if not brut.endswith(b"\n"):
            raise RegistreErreur("ligne partielle à la fin du registre")
        entrees, prec = [], ZERO
        for i, ligne in enumerate(brut[:-1].split(b"\n"), 1):
            try:
                e = json.loads(ligne)
            except ValueError:
                raise RegistreErreur(f"entrée {i} illisible") from None
            if not isinstance(e, dict) or e.get("v") != 1 or isinstance(e.get("v"), bool):
                raise RegistreErreur(f"entrée {i} : format inconnu")
            attendues = CLES_REFERENCE if e.get("type") == "reference" else CLES
            if e.get("type") not in ("ligne", "reference") or set(e) != attendues:
                raise RegistreErreur(f"entrée {i} : champs inattendus")
            if not all(_entier(e[k]) for k in ("seq", "jid", "n")):
                raise RegistreErreur(f"entrée {i} : compteurs invalides")
            if e["seq"] != i:
                raise RegistreErreur(f"entrée {i} : numéro {e['seq']} (doublon, trou ou recul)")
            if e["prec"] != prec or e["h"] != empreinte(prec, e):
                raise RegistreErreur(f"entrée {i} : chaîne du registre rompue")
            prec = e["h"]
            entrees.append(e)
        return entrees

    def verifier(self, db) -> dict:
        """Compare le registre au journal. Ne modifie rien. Toute erreur -> état non vérifié."""
        try:
            if not os.path.exists(self.chemin):
                return {"etat": ABSENT, "motif": "registre d'ancrage absent"}
            entrees = self.lire()
        except RegistreErreur as e:
            return {"etat": CORROMPU, "motif": str(e)}
        except OSError as e:
            return {"etat": INACCESSIBLE, "motif": f"registre illisible ({type(e).__name__})"}
        if not entrees:
            return {"etat": VIDE, "motif": "registre d'ancrage vide"}
        try:
            lignes = {r[0]: r[1] for r in db.execute("SELECT id, hash FROM guardian_journal ORDER BY id")}
        except Exception as e:
            return {"etat": INACCESSIBLE, "motif": f"journal illisible ({type(e).__name__})"}
        ids = sorted(lignes)
        max_id = ids[-1] if ids else 0

        def compte(jusqua: int) -> int:
            return sum(1 for i in ids if i <= jusqua)

        # 1. chaque entrée désigne une ligne qui existe encore, avec le même hash et le même compte
        for e in entrees:
            if e["jid"] > max_id:
                return {"etat": TRONQUE, "motif": f"le registre ancre la ligne {e['jid']} du journal, qui n'existe "
                                                   f"plus (journal arrêté à {max_id}) : troncature ou base restaurée"}
            if lignes.get(e["jid"]) != e["jhash"]:
                return {"etat": DIVERGENT, "motif": f"la ligne {e['jid']} du journal ne correspond plus à son ancre"}
            if compte(e["jid"]) != e["n"]:
                return {"etat": DIVERGENT, "motif": f"compte incohérent à la ligne {e['jid']} : {compte(e['jid'])} "
                                                     f"au lieu de {e['n']} (lignes supprimées avant elle)"}
        # 2. continuité depuis la dernière référence (ou depuis le début)
        r = max((i for i, e in enumerate(entrees) if e["type"] == "reference"), default=None)
        segment = entrees[r + 1:] if r is not None else entrees
        if r is None and segment[0]["jid"] != (ids[0] if ids else 0):
            debut = {"etat": PARTIEL, "motif": f"ancrage commencé à la ligne {segment[0]['jid']} : complétude "
                                               "antérieure non démontrée (aucune référence)"}
        else:
            debut = None
        attendu = entrees[r]["jid"] + 1 if r is not None else segment[0]["jid"] if segment else None
        for e in segment:
            if e["type"] != "ligne" or e["jid"] < attendu:
                return {"etat": DIVERGENT, "motif": f"ordre des ancres incohérent à la ligne {e['jid']} "
                                                     f"(attendu {attendu} ou plus)"}
            if e["jid"] > attendu:
                return {"etat": LACUNE, "motif": f"ligne(s) {attendu} à {e['jid'] - 1} du journal jamais ancrée(s) "
                                                  "(échec d'ancrage passé)"}
            attendu += 1
        dernier = segment[-1]["jid"] if segment else entrees[r]["jid"]
        if dernier < max_id:
            return {"etat": EN_RETARD, "motif": f"{max_id - dernier} ligne(s) du journal non ancrée(s) après la "
                                                f"ligne {dernier} (échec d'ancrage, ou registre restauré ancien)",
                    "dernier_ancre": dernier, "debut": debut}
        if debut:
            return debut
        if r is not None:
            return {"etat": COMPLET_REF, "motif": "complet depuis la référence non vérifiée",
                    "garantie_depuis": entrees[r]["quand"], "reference_jid": entrees[r]["jid"]}
        return {"etat": COMPLET, "motif": "complet depuis la première ligne du journal",
                "garantie_depuis": entrees[0]["quand"]}
