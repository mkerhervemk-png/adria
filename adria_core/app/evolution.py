"""Mémoire évolutive (lot E1, 19/11/2026) : le journal « erreur → correction » d'ADRIA.

- Chaque problème repéré par le Core ouvre une ligne (« ouverte ») ; quand il disparaît, la ligne est
  fermée avec ce qui l'a corrigé (« corrigée »). Même problème encore ouvert = compteur `fois` + 1.
- Alimenté sans IA (0 token) par : consolidation de nuit, recopie 03h30, purges, maintenance 06h30,
  recherche par le sens, reconnexion après un redémarrage de HA.
- Mael (ou Claude) peut ajouter une leçon : événement `adria_core_correction` {erreur, correction, domaine}.
- Les leçons connues avant ce lot sont reprises une fois (LECONS_DEPART).
- Chaque leçon corrigée est aussi copiée dans la mémoire (source « evolution ») : ADRIA la retrouve
  à la voix (« qu'est-ce que tu as corrigé sur la consolidation ? »).
- Rien n'est jamais effacé (règle du 17/11).
"""

import logging
from datetime import datetime

LOG = logging.getLogger("adria_core.evolution")

CAPTEUR = "sensor.adria_core_evolution"

LECONS_DEPART = [
    ("histo_iwan_inconnu", "présence", "Faux départs d'Iwan chaque nuit vers 05h42",
     "passages vers/depuis « inconnu » de sensor.adria_iwan_lieu", "O5 ignore les passages « inconnu »", "2026-11-18"),
    ("histo_evenement_modele", "voix", "Les étapes vocales J1 et C1 n'envoyaient jamais leur événement",
     "HA ne remplace pas les modèles dans le nom d'un événement", "toujours un nom d'événement fixe (choose par cas)",
     "2026-11-18"),
    ("histo_consolidation_0215", "mémoire", "Consolidation de nuit 02h15 en échec (« IA sans réponse »)",
     "IA indisponible à cette heure-là", "2e essai automatique à 02h45 si la 1re a échoué", "2026-11-19"),
    ("histo_sauvegarde_nuit", "déploiement", "Mises à jour refusées (HTTP 503) entre 05h00 et 05h45",
     "sauvegarde HA de nuit", "ne jamais déployer pendant ce créneau", "2026-11-17"),
    ("histo_capteurs_redemarrage", "Core", "Capteurs du Core (adria_core, maintenance, habitudes…) disparus après "
     "un redémarrage de HA", "un capteur créé par l'API n'est pas gardé par HA au redémarrage",
     "le Core republie tous ses capteurs à chaque reconnexion (v0.17.0)", "2026-11-19"),
]


class Evolution:
    def __init__(self, memoire):
        self.m = memoire
        self.m.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS evolution (
                id INTEGER PRIMARY KEY, cle TEXT NOT NULL, domaine TEXT, erreur TEXT NOT NULL, cause TEXT,
                correction TEXT, statut TEXT NOT NULL, fois INTEGER NOT NULL DEFAULT 1,
                ouverte_le TEXT NOT NULL, corrigee_le TEXT, source TEXT
            );
            CREATE INDEX IF NOT EXISTS evolution_cle ON evolution (cle, statut);
            """)
        for cle, dom, err, cause, corr, le in LECONS_DEPART:
            if not self.m.db.execute("SELECT 1 FROM evolution WHERE cle = ?", (cle,)).fetchone():
                self.m.db.execute(
                    "INSERT INTO evolution (cle, domaine, erreur, cause, correction, statut, ouverte_le,"
                    " corrigee_le, source) VALUES (?, ?, ?, ?, ?, 'corrigée', ?, ?, 'reprise')",
                    (cle, dom, err, cause, corr, le, le))
                self._copier(err, corr, le)
        self.m.db.commit()

    def _copier(self, erreur: str, correction: str, quand: str) -> None:
        self.m.ajouter("evolution", [{"summary": f"Erreur corrigée : {erreur} → {correction}",
                                      "date": quand[:10]}])

    def signaler(self, cle: str, domaine: str, erreur: str, cause: str | None = None,
                 source: str = "auto", quand: datetime | None = None) -> int:
        q = (quand or datetime.now()).isoformat(timespec="seconds")
        r = self.m.db.execute("SELECT id FROM evolution WHERE cle = ? AND statut = 'ouverte'", (cle,)).fetchone()
        if r:
            self.m.db.execute("UPDATE evolution SET fois = fois + 1, erreur = ? WHERE id = ?", (erreur[:300], r[0]))
            self.m.db.commit()
            return r[0]
        cur = self.m.db.execute(
            "INSERT INTO evolution (cle, domaine, erreur, cause, statut, ouverte_le, source)"
            " VALUES (?, ?, ?, ?, 'ouverte', ?, ?)", (cle, domaine, erreur[:300], cause, q, source))
        self.m.db.commit()
        LOG.info("Évolution : ouverte « %s »", erreur[:120])
        return cur.lastrowid

    def corriger(self, cle: str, correction: str, quand: datetime | None = None) -> bool:
        q = (quand or datetime.now()).isoformat(timespec="seconds")
        r = self.m.db.execute("SELECT id, erreur FROM evolution WHERE cle = ? AND statut = 'ouverte'",
                              (cle,)).fetchone()
        if not r:
            return False
        self.m.db.execute("UPDATE evolution SET statut = 'corrigée', correction = ?, corrigee_le = ? WHERE id = ?",
                          (correction[:300], q, r[0]))
        self.m.db.commit()
        self._copier(r[1], correction, q)
        LOG.info("Évolution : corrigée « %s »", r[1][:120])
        return True

    def suivre(self, cle: str, domaine: str, ok: bool, erreur: str, correction: str) -> None:
        """Raccourci : ok -> ferme s'il y avait une ligne ouverte ; sinon ouvre/compte."""
        if ok:
            self.corriger(cle, correction)
        else:
            self.signaler(cle, domaine, erreur)

    def lecon(self, data: dict, quand: datetime | None = None) -> dict:
        """Leçon donnée par Mael ou Claude (événement adria_core_correction)."""
        erreur = str(data.get("erreur") or "").strip()
        correction = str(data.get("correction") or "").strip()
        if len(erreur) < 4:
            return {"ok": False, "texte": "Dis-moi quelle était l'erreur."}
        cle = "lecon_" + datetime.now().strftime("%Y%m%d%H%M%S")
        self.signaler(cle, str(data.get("domaine") or "général"), erreur, data.get("cause"), "mael", quand)
        if correction:
            self.corriger(cle, correction, quand)
            return {"ok": True, "texte": "C'est noté : je retiens l'erreur et sa correction."}
        return {"ok": True, "texte": "C'est noté : l'erreur reste ouverte jusqu'à sa correction."}

    def lister(self, statut: str | None = None, n: int = 20) -> list[dict]:
        sql = "SELECT * FROM evolution"
        params: tuple = ()
        if statut:
            sql += " WHERE statut = ?"
            params = (statut,)
        rows = self.m.db.execute(sql + " ORDER BY COALESCE(corrigee_le, ouverte_le) DESC LIMIT ?",
                                 (*params, max(1, min(int(n), 100)))).fetchall()
        return [dict(r) for r in rows]

    def etat(self) -> dict:
        ouvertes = self.lister("ouverte", 10)
        total = self.m.db.execute("SELECT statut, COUNT(*) FROM evolution GROUP BY statut").fetchall()
        return {"state": len(ouvertes), "attributes": {
            "friendly_name": "ADRIA Core — erreurs et corrections", "icon": "mdi:school-outline",
            "unit_of_measurement": "ouvertes",
            "par_statut": {r[0]: r[1] for r in total},
            "ouvertes": [f"{o['ouverte_le'][:16]} [{o['domaine']}] {o['erreur']} (x{o['fois']})" for o in ouvertes],
            "dernieres_corrections": [f"{c['corrigee_le'][:10]} {c['erreur']} → {c['correction']}"
                                      for c in self.lister("corrigée", 5)]}}
