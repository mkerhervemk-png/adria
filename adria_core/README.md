# ADRIA Core (v0.1.0)

Mémoire longue d'ADRIA, sans IA.

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
  exclure_sources: [adria_memoire_perso]   # facultatif
```

Réponse : événement `adria_core_resultat` avec `req_id`, `ok`, `lignes` (source, date, texte, detail) et `texte` (2 000 caractères maximum).

Forcer une synchronisation : événement `adria_core_synchroniser`.

## Confidentialité

La mémoire personnelle (`adria_memoire_perso`) contient des lignes par personne. Pour un enfant ou une voix inconnue, l'appelant doit passer `exclure_sources: [adria_memoire_perso]`.

## Tests

```
cd adria_core && python3 -m pytest tests
```
