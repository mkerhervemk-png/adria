# Jarvis — appli Android pour Home Assistant

Appli native (Java, sans dépendance externe) qui envoie une phrase dictée ou écrite à
`POST /api/conversation/process`, puis affiche et lit la réponse de l'agent (ADRIA).

## Obtenir l'APK sans rien installer (GitHub Actions)
1. Sur GitHub, crée une branche `jarvis-apk` dans un dépôt (ex : `adria`).
2. Dépose le dossier `jarvis-android/` à la racine, et le fichier
   `jarvis-android/.github/workflows/jarvis-apk.yml` dans `.github/workflows/` à la racine.
3. Onglet **Actions** → « Build Jarvis APK » → la compilation dure environ 3 min →
   télécharge l'artefact **Jarvis-apk** (un zip qui contient `Jarvis.apk`).

## Ou avec Android Studio
Ouvre le dossier `jarvis-android/`, puis Build → Build APK(s).

## Installation
Autorise « Sources inconnues » pour ton navigateur ou ton gestionnaire de fichiers, puis ouvre `Jarvis.apk`.
Réglages ⚙ : l'URL de HA (IP locale `http://192.168.1.x:8123` ou Nabu Casa), le Long-Lived Token,
l'agent_id d'ADRIA.
