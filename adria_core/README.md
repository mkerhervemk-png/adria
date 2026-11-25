# ADRIA Core (v0.22.1)

**Règle permanente : le Core n'efface jamais une ligne** (test automatique : aucun DELETE/DROP/TRUNCATE dans le code).

Cerveau d'ADRIA hors de Home Assistant : mémoire longue (SQLite + recherche par mots et par le sens),
routeur IA, carnet de bord, purges des listes HA, consolidation de nuit, formulaires PDF, courriers et
échéances, papiers, budget, menus, devoirs, maintenance, habitudes, procédures, journal erreur → correction
et bus MCP en lecture seule.

## Nouveau en 0.22.1 — correctif de l'audit de la v0.22.0 (sécurité du Guardian et du banc)

- **Arrêt d'urgence fermé par défaut** : `input_boolean.adria_core_arret_urgence` bloque tout (sauf
  `etat_core`) dès que son état n'est pas exactement la chaîne `off` : fonction de lecture absente, `None`,
  `on`, `unknown`, `unavailable`, exception ou réponse d'un autre type = arrêt actif (révision 2).
- **Journal obligatoire** : une décision qui n'a pas pu être écrite dans `guardian_journal` devient un refus.
- **Filtre central des entités sensibles** (`guardian.entite_autorisee`) appliqué à toutes les classes
  (lecture comprise) et à tous les chemins (entity_id exact, recherche par nom, historique) : alarme,
  serrures, caméras, sirènes, mandats, tous les `input_*.adria_*` (dont les `input_boolean.adria_core_*`)
  et tout `*.adria_core_*`. Deuxième barrière dans BRAIN.
- **Profils** : le profil vient de `qui` ; un `profil` fourni ne peut que restreindre (le plus strict gagne).
- **Preuves** : une preuve citée qui ne partage aucun terme significatif avec la réponse est écartée
  (`preuves_ecartees`) ; sans preuve pertinente, la réponse est au mieux « je n'en suis pas sûre ».
- **Banc** : 2 passages par jour au plus (compteur dans la base, réservé avant le passage, dans une
  transaction `BEGIN IMMEDIATE` : pas de dépassement en cas d'appels simultanés). **Fermé par défaut**
  (révision 2) : compteur illisible, corrompu, négatif, d'un type inattendu, daté dans le futur, ou erreur
  SQLite = refus ; remise à zéro seulement si la date enregistrée (valide) est antérieure à aujourd'hui. Plus aucune
  écriture dans le journal des erreurs, le registre des contradictions ni l'historique des missions.
- **Ancre du journal** : la tête de la chaîne (`journal_ancre`) est publiée dans `sensor.adria_core_brain`
  après chaque mission, chaque banc et à 23h55 ; une chaîne entièrement recalculée ne correspond plus aux
  ancres gardées dans l'historique de HA.

### Quota du banc : initialisation prouvée (révision 3)
L'absence du compteur n'est plus prise pour une première utilisation. Deux clés `meta` sont écrites **dans
la même transaction** que chaque réservation : `banc_initialise` (marqueur, jamais réécrit) et
`banc_compte` (`{"date", "n", "v": 2}`), plus une ligne `banc_reservation` dans le journal chaîné du
Guardian (témoin indépendant).

| État trouvé | Décision |
|---|---|
| A. ni marqueur, ni compteur, aucune trace (historique du banc, réservation au journal, journal rompu) | base vierge : marqueur + compteur créés, passage accordé |
| B. ancien compteur `{"date", "n"}` valide, sans marqueur (premières v0.22.1) | migration : marqueur `migration`, consommation du jour conservée |
| C. marqueur sans compteur | refus, rien n'est recréé |
| D. marqueur ou compteur invalide, ou incohérents (compteur v2 sans marqueur…) | refus, aucune réparation, motif dans le journal de l'appli |
| E. ni marqueur ni compteur, mais une trace d'usage | refus ; seule la récupération manuelle débloque |

**Une base v0.22.0 qui a déjà fait tourner le banc est dans le cas E** (elle a `banc_historique` mais ni
compteur ni marqueur) : après l'installation de la 0.22.1, le banc reste refusé jusqu'à la récupération.

### Registre d'ancrage du journal du Guardian (révision 6)
`/media/adria_core_ancres/guardian.jsonl` (`app/ancrage.py`) : une ligne JSON par écriture du journal, ajoutée
juste après son commit (verrou `fcntl` commun pris avant la transaction, `fsync`). Chaque entrée : `seq`
(1, 2, 3…), `type` (`ligne` | `reference`), `jid` + `jhash` (ligne du journal ancrée), `n` (lignes du journal),
`quand`, `origine`, `source`, `prec` + `h` (chaîne propre du registre).
- **Rôle** : prouver, à partir d'une date connue, qu'aucune ligne du journal n'a disparu (troncature,
  restauration d'une ancienne sauvegarde de l'appli, perte). **Pas** une protection contre une compromission
  du conteneur ou de HA : le même conteneur écrit la base et le registre.
- **Support** : `/media` est un autre dossier que `/data` mais, sous Home Assistant OS, sur le **même disque
  physique** (partition de données). Une sauvegarde **partielle** de l'appli ne contient que `/data` ; une
  sauvegarde **complète** contient aussi le dossier `media`. Le Core écrit déjà dans `/media` en production
  (classement A3, formulaires). Une sonde au démarrage vérifie l'accès au dossier (journal de l'appli).
- **Panne du registre** : la décision du Guardian reste valable (journalisée), BRAIN continue ; la ligne
  n'est pas ancrée (`ancrage_en_retard`, attribut `ancrage` de `sensor.adria_core_brain`) et seule la
  récupération du banc devient impossible. Le registre n'est jamais réparé ni reconstruit.
- **États** : `complet`, `complet_depuis_reference` (preuve) ; `absent`, `vide`, `partiel`, `en_retard`,
  `lacune` (absence de preuve) ; `corrompu`, `tronque`, `divergent`, `inaccessible` (incohérence).

**Récupération manuelle VÉRIFIÉE** (intervention explicite de Mael, jamais faite par le Core) : seulement sur
un cas E prouvé (marqueur et compteur absents, trace d'usage, journal présent/non vide/intègre) **et** un
registre `complet` ou `complet_depuis_reference`. Depuis l'appli « Advanced SSH & Web Terminal » :

```
docker exec addon_0daf054b_adria_core python3 -c "import sys; sys.path.insert(0, '/app'); from store import Memoire; import banc, ancrage; print(banc.recuperer(Memoire('/data/adria_core.db'), banc.CONFIRMATION, registre=ancrage.Registre()))"
```

**Migration NON VÉRIFIÉE** (décision de Mael du 25/11, option b — base sans ancrage historique, comme la
production v0.22.0) : deux phrases exactes, mêmes contrôles du cas E, registre en « absence de preuve »
uniquement (jamais sur une incohérence prouvée), une seule fois (une 2e exige `NOUVELLE_AUTORISATION`).
Écrit le marqueur `recuperation_non_verifiee`, un compteur « quota du jour épuisé », une ligne au journal, puis
une entrée `reference` du registre qui note ce qui a été observé (dernière ligne, hash, nombre de lignes).
**La complétude du journal AVANT cette opération n'est pas démontrée** ; la garantie court à partir d'elle.

```
docker exec addon_0daf054b_adria_core python3 -c "import sys; sys.path.insert(0, '/app'); from store import Memoire; import banc, ancrage; print(banc.recuperer_non_verifiee(Memoire('/data/adria_core.db'), banc.CONFIRMATION, banc.RECONNAISSANCE, registre=ancrage.Registre()))"
```

Aucune des deux n'est reliée à un événement, un outil MCP, BRAIN ou un agent. Aucun passage n'est accordé le
jour même ; le banc revient le lendemain.

### Limites connues (v0.22.1)
- Protection contre la perte ou la corruption ACCIDENTELLE du quota : quelqu'un qui peut écrire dans la base
  du Core peut aussi y écrire un compteur valide ; le journal chaîné et son ancre dans HA permettent de le
  constater après coup.
- Retour arrière : la 0.22.0 ignore le compteur et n'ancre pas ; les bancs lancés en 0.22.0 ne sont pas
  comptés, et ses lignes de journal laissent une `lacune` au retour en 0.22.1 (seule la récupération est
  alors impossible sans nouvelle migration non vérifiée explicitement autorisée).
- Registre : même disque physique que la base sous HAOS ; une restauration COMPLÈTE de HA ramène base et
  registre au même instant (cohérents entre eux, donc non détectée : c'est un retour arrière voulu).
- Une base dont le journal du Guardian est rompu, absent ou vide ne peut pas récupérer le quota du banc : le banc y reste
  refusé tant que la rupture n'a pas été examinée à la main (aucune procédure automatique prévue).
- Le Guardian est un module du même processus que le Core : il protège contre une mauvaise sortie d'un
  modèle (qui ne peut pas exécuter de code), pas contre un Core compromis.
- Le Core ne peut pas authentifier une voix : il fait confiance à `qui` envoyé par HA (bus interne, réservé
  à HA et à ses administrateurs).
- La pertinence des preuves est lexicale (termes communs) : elle écarte les preuves hors sujet, elle ne
  prouve pas qu'une preuve implique logiquement la réponse.
- L'ancre est gardée par l'historique de HA (recorder) : quelqu'un qui contrôle à la fois la base du Core et
  celle de HA peut tout réécrire.
- Un compteur du banc corrompu bloque le banc jusqu'à sa correction manuelle (clé `meta.banc_compte`) :
  c'est voulu (fermé par défaut), il n'existe pas de remise à zéro automatique.
- Le banc mesure le système réel : il consomme de vrais appels IA (compteur Gemini du jour compris) et écrit
  dans le journal du Guardian et les statistiques des moteurs (volontairement).

### Retour arrière vers 0.22.0
Aucun changement de schéma : seules une clé `meta` (`banc_compte`) et les tables de la 0.22.0 sont utilisées.
Revert du commit de la 0.22.1 sur `adria-core` (version 0.22.2 ou réinstallation de 0.22.0), la base reste
lisible par la 0.22.0 (vérifié : journal chaîné intègre, BRAIN et banc fonctionnels sur la base de la 0.22.1).

## Nouveau en 0.22.0 — ASCENSION S1 à S4 (additif, tout est éteint ou en lecture seule par défaut)

- **S3 — GUARDIAN** (`app/guardian.py`) : autorité indépendante, **sans IA**. Registre fixe des outils
  (classe + profils) ; refus par défaut ; profil invalide = refus ; les actions réelles ne sont jamais
  exécutées par BRAIN (OUI de Mael requis) ; **exclusion permanente** : alarme, serrures, sirène, caméras,
  mandats, interrupteurs `input_boolean.adria_core_*`, désarmement. Arrêt d'urgence :
  `input_boolean.adria_core_arret_urgence` ON → tout refusé sauf `etat_core`. Panne = refus (tombe fermé).
  8 outils max par mission. **Journal chaîné par hash** (`guardian_journal`) : toute retouche est détectée.
- **S2 — BRAIN v1** (`app/brain.py`) : événement `adria_core_brain` {question, qui | profil, voix, req_id}
  → `adria_core_brain_resultat`. Mémoire d'abord (0 IA), puis 2 à 3 tours où le modèle choisit des outils
  **en lecture seule** (mémoire, période, procédures, journal des erreurs, état du Core, carnet, habitudes,
  agenda, vue du foyer, état et historique d'un appareil), chacun validé par le Guardian. Vérification sans
  IA : preuves citées `[M1]`/`[O1]`, statut `sait` / `suppose` / `ignore`, contradictions. Réponse sourcée ;
  une action n'est qu'une **proposition**. Personne identifiée → profil `inconnu`. 60 missions/jour.
  Les réponses de BRAIN ne sont pas écrites dans la mémoire longue (pas d'auto-renforcement).
  Interrupteur `input_boolean.adria_core_brain` (absent = éteint). Capteur `sensor.adria_core_brain`.
- **S4 — mémoire fiable** (`app/fiabilite.py`) : nature (observé / déclaré / raconté / déduit) et
  confiance calculées sans IA pour chaque ligne ; une réponse d'IA (`general`) n'est jamais un fait sûr ;
  les états volatils vieillissent. Contradictions repérées → table `contradictions` (ajout seul, statut
  « tranchée » quand Mael décide). La table `lignes` n'est pas modifiée.
- **S1 — banc d'évaluation** (`app/banc.py`) : 25 scénarios (honnêteté, mémoire, outils, raisonnement,
  sécurité, profils) notés sans IA. Événement `adria_core_banc` {moteur: "" | gemini | gemini_lite | ollama,
  categorie} → `sensor.adria_core_banc` (score %, par catégorie, échecs, historique des 10 passages).
  Le banc ignore l'interrupteur et le quota de BRAIN mais ne fait jamais d'action.
- Retour arrière : `input_boolean.adria_core_brain` OFF (BRAIN muet) ; ou revenir à v0.21.3.
  Rien d'existant n'est modifié : routeur, mémoire, MCP, agents et interrupteurs restent identiques.

## Nouveau en 0.21.3

- Profil `enfant` : archives des épisodes (`archive/episode`) et du journal intérieur
  (`archive/journal_interieur`) ajoutées, comme la règle validée le 17/11. `script.avatar_demander` envoie
  désormais `profil` (adulte / enfant / inconnu) via `script.adria_core_chercher`.

## Nouveau en 0.21.2 — sécurité (dossier de remédiation du 24/11/2026)

- **R1 — mémoire cloisonnée par profil, côté serveur** (`app/confidentialite.py`). Profils `adulte`, `enfant`,
  `inconnu`. Les profils restreints lisent seulement une liste blanche de sources (refus par défaut : une
  source nouvelle est fermée). Le client ne peut qu'exclure *plus*, jamais moins. Double barrière : avant la
  requête (exclusions) et après (filtre des lignes, recherche par le sens comprise).
  - Événement HA `adria_core_recherche` : champ `profil` (facultatif). Sans lui, l'ancien
    `script.adria_core_chercher` (`restreint: true` → exclusion de la mémoire perso) est reconnu et traité
    comme `inconnu`, le plus strict. Sans exclusion : `adulte`, comme avant.
  - MCP : chaque jeton porte un profil (option `mcp_jetons`). Un jeton sans profil valide est refusé.
    L'ancien `mcp_jeton` reste accepté avec le profil `mcp_jeton_profil` (`adulte` si absent).
    `carnet_dernier` et `habitudes` sont réservés au profil `adulte`.
- **R2 — consolidation** : `forcer=true` ne contourne plus `input_boolean.adria_core_consolidation`.
  Tout est préparé sans écrire (tri, IA, garde-fou, interdits) ; les deux interrupteurs sont relus juste
  avant l'écriture ; les nouveaux faits sont ajoutés d'abord, et retirés si un ajout échoue (compensation),
  avant de toucher aux anciens.
- **R5 — purge** : `essai: true` est une simulation stricte (aucune écriture SQLite, aucune suppression,
  `sensor.adria_core` inchangé). Le rapport (`retirees`, `a_copier`, `gardees_sans_copie`, `operations`,
  `erreurs`) arrive par l'événement `adria_core_purge_resultat`.
- **R6 — interdits** : une seule liste de sujets interdits (décision du 13/10) avec synonymes et
  reformulations, comparée sans accents ni ponctuation, appliquée avant toute écriture.
- **R7 — CI** : `.github/workflows/adria-core-tests.yml` lance l'analyse et tous les tests à chaque PR ;
  un test qui échoue bloque. Aucune publication depuis ce workflow.

## Interrupteurs (Home Assistant)

Chaque fonction a son `input_boolean.adria_core_*` ; OFF = la fonction ne fait rien. Une lecture de HA
impossible compte comme OFF. Principaux garde-fous :

| Interrupteur | Rôle |
|---|---|
| `adria_core_purge` | autorise la purge réelle des listes HA (03h30) |
| `adria_core_consolidation` | maître de la consolidation de nuit (essai si seul) |
| `adria_core_consolidation_reelle` | avec le maître : écriture réelle dans `todo.adria_memoire` |
| `adria_core_memoire` | carnet de bord du soir |
| `adria_core_sens` | recherche et indexation par le sens (BGE-M3 via Ollama) |
| `adria_core_procedures` | mémoire des procédures |

Autres : `analyse`, `bilan`, `budget`, `classement`, `competences`, `courriers`, `devoirs`, `famille`,
`formulaires`, `general`, `habitudes`, `journal`, `maintenance`, `menus`, `monde`, `notifications`,
`papiers`, `passe`, `suivis`, `veille`.

## Prérequis et compatibilité

- Home Assistant OS ou Supervised (appli Supervisor, `homeassistant_api: true`), architecture amd64.
- Facultatif : Ollama (`ollama_url`) pour le secours IA et la recherche par le sens.
- Python des tests : 3.11+ avec `aiohttp`, `pypdf`, `reportlab`, `pytest` ; `poppler-utils` pour les PDF.

## Sauvegarde et restauration

- La base est `/data/adria_core.db` de l'appli : elle est incluse dans une sauvegarde HA de l'appli
  « ADRIA Core » (sauvegarde partielle possible).
- Avant une mise à jour : sauvegarde partielle de l'appli, puis vérifier qu'elle apparaît dans la liste.
- Restaurer : arrêter l'appli, restaurer la sauvegarde partielle, redémarrer, vérifier `sensor.adria_core`
  (état `ok`, `lignes`, `version`). Ne jamais copier une ancienne base par-dessus la base active pendant
  que l'appli tourne.
- Retour à la version précédente : réinstaller le dossier `adria_core` de la version d'avant (tag ou
  commit) puis restaurer la sauvegarde partielle prise avant la mise à jour.

## Limites connues

- Le profil reçu par l'événement HA repose sur ce qu'envoie HA (bus interne, réservé aux utilisateurs et
  intégrations de HA) : la reconnaissance de la voix reste faite par HA.
- La compensation de la consolidation retire les faits ajoutés par leur texte : un fait identique déjà
  présent pourrait être retiré à sa place (le contenu reste le même).
- Les interdits restent une liste de motifs : un sujet vraiment nouveau doit y être ajouté.

## Historique

Nouveau en 0.4.1 — filet de sécurité : signal de vie (attribut `battement` de `sensor.adria_core_ia`, chaque minute ; HA saute le Core s'il a plus de 3 min) ; mode test `seulement: <moteur>` dans `adria_core_ia`.

Nouveau en 0.4.0 — agent Mémoire (lot M2a) :
- carnet de bord du soir + réflexion d'ADRIA faits par le Core à 23h40 (option `heure_carnet`) si `input_boolean.adria_core_memoire` est ON (et `adria_jarvis_actif` ON) : même prompt et même sortie que l'automatisation HA « Jarvis P4 », avec en plus PRESENCES_REELLES tirées de l'historique du Core ; un seul appel IA via le routeur du Core ;
- événement `adria_core_carnet` {essai: true|false, forcer} → `adria_core_carnet_resultat` ; `essai` n'écrit rien dans HA ; dernier résultat dans `sensor.adria_core` (attribut dernier_carnet).

Nouveau en 0.3.1 — recherche par période (agent Mémoire, lot M1) :
- une question qui nomme une période (« en octobre », « la semaine dernière », « le week-end dernier », « mardi dernier », « hier », « le 24/10 », « le 24 octobre ») rend les lignes datées de cette période : souvenirs et carnets de bord d'abord, puis journal, témoin, historique, observations ; triées par date ;
- s'il reste un sujet dans la question (« en octobre, l'Inster »), les lignes de la période qui en parlent passent en premier ;
- sans période : recherche par mots comme avant.

Nouveau en 0.3.0 — routeur IA :
- événement `adria_core_ia` {req_id, prompt, prompt_secours, profondeur (interactif|rapide|profond), task_name, force_gemini} → réponse `adria_core_ia_resultat` {req_id, ok, data, moteur, duree} ;
- même chaîne que le routeur YAML du 11/11 (Gemini → Gemini Lite → [Claude si interrupteur ON] → Ollama ; repli à la voix) ; Ollama appelé en direct (llama3.2:3b, arrêté vraiment au délai), sinon via ai_task.adria_secours ;
- coupe-circuit : 3 échecs en 10 min → moteur sauté 15 min ;
- statistiques par moteur (24 h / 7 j) et coupe-circuit dans `sensor.adria_core_ia`.

Nouveau en 0.2.0 :
- relit l'archive `/media/adria_memoire/*.md` (lignes « [AAAA-MM-JJ] [source] texte », sources `archive/...`) avec la vraie date de chaque ligne ;
- garde l'historique Home Assistant des entités de l'option `historique` (motifs acceptés, ex. `light.*`) : 14 jours au premier lancement, puis chaque nuit depuis le dernier import (source `historique`), donc au-delà de la durée du recorder ;
- une phrase présente à la fois dans une liste et dans l'archive n'est rendue qu'une fois ;
- la date renvoyée est celle de l'événement quand elle est connue (sinon vide).

- Recopie chaque nuit (03h30 par défaut) les listes ADRIA de Home Assistant dans une base SQLite. Rien n'est jamais effacé, même quand ADRIA purge ses listes après 48 h ou 7 jours.
- Répond aux recherches par mots sur tout cet historique.
- Facultative : si l'appli est arrêtée, ADRIA fonctionne exactement comme avant.

## Installation (appli locale, la plus simple pour un dépôt privé)

1. Ouvrir le partage Samba de Home Assistant, dossier `addons`.
2. Y copier le dossier `adria_core` en entier (celui qui contient `config.yaml`).
3. Home Assistant → Paramètres → Applications → Boutique → menu ⋮ → « Rechercher des mises à jour ».
4. « ADRIA Core » apparaît dans « Applications locales » : Installer, puis Démarrer.
5. Vérifier `sensor.adria_core` : état `ok`, attribut `lignes` > 0 après une minute.

## Utilisation depuis Home Assistant

Le script `script.adria_core_chercher` envoie l'événement `adria_core_recherche` et attend la réponse (5 s maximum).

Événement envoyé :

```yaml
event: adria_core_recherche
event_data:
  req_id: "abc123"
  question: "qu'avait-on décidé pour le préchauffage ?"
  n: 5
  profil: adulte                           # adulte | enfant | inconnu (0.21.2)
  exclure_sources: [adria_memoire_perso]   # facultatif : exclusions EN PLUS de la politique
```

Réponse : événement `adria_core_resultat` avec `req_id`, `ok`, `lignes` (source, date, texte, detail) et `texte` (2 000 caractères maximum).

Forcer une synchronisation : événement `adria_core_synchroniser`.

## Confidentialité

Depuis 0.21.2, c'est le Core qui décide (voir R1 plus haut et `app/confidentialite.py`). L'appelant
indique le profil (`profil: adulte|enfant|inconnu`) ; `exclure_sources` peut seulement retirer des sources
en plus. Listes blanches modifiables par les options `memoire_sources_enfant` et `memoire_sources_inconnu`.

## Tests

```
cd adria_core && python3 -m pytest tests
```

## Formulaires PDF (v0.6.0, lot F1)
Interrupteur `input_boolean.adria_core_formulaires`. Envoyer le PDF depuis la page « ADRIA Formulaires » du menu HA (Ingress, v0.6.1 ; les Médias HA refusent les PDF).
Gemini Vision repère les champs ; profil de l'appli (onglet Configuration > `formulaire_profil`) + date du jour
remplis seuls ; le reste est demandé par notification (boutons OUI/NON/Tout NON ou Répondre).
Réponses gardées en mémoire vive uniquement (ni base, ni journaux). Résultat : `adria_formulaires/sortie`
(PDF + aperçu PNG), sans signature, jamais envoyé à personne. Originaux rangés dans `entree/traites` ou `entree/echecs`.
État : `sensor.adria_core_formulaires`.

## Courriers et échéances (v0.7.0, lot A1)
Interrupteur `input_boolean.adria_core_courriers`. Même page, mode « Courrier reçu » : PDF (3 pages max) ou photo
JPG/PNG/WEBP. Un seul appel Gemini Vision par courrier : expéditeur, objet, résumé, 3 échéances max (futures).
Notification avec le résumé, puis pour chaque date OUI / NON / Modifier la date (réponse jj/mm[/aaaa] [14h30]).
OUI -> `calendar.create_event` dans `courrier_calendrier` (défaut `calendar.mael_adria`), description
« Courrier lu par ADRIA le … ». Rappel J-3 chaque jour à `courrier_heure_rappel` (défaut 10:00) en relisant l'agenda.
Résumé en mémoire vive uniquement. Fichiers : `adria_formulaires/courriers/entree` (+ `traites`/`echecs`, jamais effacés).

## Formulaires pour tout le foyer (v0.8.0, lot F2)
Option `formulaire_foyer` (onglet Configuration) : un membre par entrée (prenom, nom, naissance, lieu_naissance,
etablissement, telephone, email, responsable). Mael = `formulaire_profil`, toujours responsable légal n°1.
Page : « Pour qui ? » → le fichier est déposé en `@Prénom@nom.pdf`. Gemini donne à chaque champ un rôle
(`personne` / `responsable1` / `responsable2`). Adresse du foyer pour tous ; téléphone/email du foyer seulement
pour un adulte responsable (jamais recopiés pour un enfant) ; nom + prénom écrits seulement si les deux sont connus.

## Classement des papiers (v0.9.0, lot A3)
Interrupteur `input_boolean.adria_core_classement`. Après chaque formulaire rempli (PDF rempli) et chaque courrier
lu (original), une copie va dans `/media/adria_documents/<Personne>/<Type>/<AAAA-MM-JJ>_<titre>.<ext>` puis sur
OneDrive (`onedrive_entree`, dossier Apps/Home Assistant/ADRIA/documents/<Personne>/<Type>). Type donné par Gemini
dans l'appel existant (école, santé, assurance, impôts, banque, voiture, maison, sport, travail, autre) ; courrier :
destinataire parmi les prénoms du foyer, sinon « Foyer ». Copies seulement, jamais d'écrasement (_2, _3…).
