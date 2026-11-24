# ADRIA Core (v0.21.3)

**Règle permanente : le Core n'efface jamais une ligne** (test automatique : aucun DELETE/DROP/TRUNCATE dans le code).

Cerveau d'ADRIA hors de Home Assistant : mémoire longue (SQLite + recherche par mots et par le sens),
routeur IA, carnet de bord, purges des listes HA, consolidation de nuit, formulaires PDF, courriers et
échéances, papiers, budget, menus, devoirs, maintenance, habitudes, procédures, journal erreur → correction
et bus MCP en lecture seule.

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
