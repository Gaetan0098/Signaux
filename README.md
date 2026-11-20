# Signaux MM20/MM200

Chaque soir de bourse, GitHub récupère tout seul les cours des ~500 actions du S&P 500.
L'application, installée sur votre téléphone, affiche le top 10 avec les signaux
Acheter / Attendre / Vendre. Le bouton « Mise à jour » va chercher les derniers calculs.
Le dossier `backtest/` sert à tester les règles sur 5 ans (voir `backtest/LISEZMOI.md`).

## Mise en place (une seule fois, depuis un ordinateur)

1. **Créez un compte** sur github.com : « Sign up », e-mail, mot de passe, nom d'utilisateur.
   Ce nom apparaîtra dans l'adresse de votre appli.
2. **Créez le dépôt** : bouton « + » en haut à droite › « New repository ».
   - Repository name : `signaux`
   - Cochez **Public**
   - Cochez « Add a README file »
   - Cliquez sur « Create repository ».
3. **Envoyez les fichiers** : dans le dépôt, « Add file » › « Upload files ». Glissez dans la
   page **le contenu** du dossier dézippé : les dossiers `backtest`, `data`, `icons`,
   `.github` et les fichiers, pas le dossier qui les contient. Validez avec
   « Commit changes ».
   - Sur Mac, le dossier `.github` est caché : appuyez sur Cmd+Maj+. dans le Finder.
   - Vérifiez qu'il apparaît bien dans le dépôt après l'envoi. Sinon, voir l'aide en bas de page.
4. **Autorisez les scripts à écrire** : Settings › Actions › General ›
   Workflow permissions › « Read and write permissions » › Save.
5. **Activez l'hébergement** : Settings › Pages › Source « Deploy from a branch »,
   branche `main`, dossier `/ (root)` › Save. Au bout d'une minute, l'adresse s'affiche :
   `https://votre-nom.github.io/signaux/`
6. **Premiers téléchargements des cours** : onglet Actions › « Mise à jour des cours » ›
   « Run workflow ». Attendez la coche verte, quelques minutes.
7. **Installez l'appli** : ouvrez l'adresse de l'étape 5 sur votre téléphone.
   - iPhone (Safari) : Partager › « Sur l'écran d'accueil »
   - Android (Chrome) : menu ⋮ › « Installer l'application »

Ensuite, tout est automatique. Les cours se mettent à jour du lundi au vendredi à 22h30 UTC.

## Bon à savoir

- **Votre portefeuille reste sur votre téléphone** : rien n'est envoyé à GitHub.
- **Si une mise à jour échoue** (Yahoo Finance bloque parfois), GitHub vous prévient par
  e-mail et l'appli garde les cours de la veille. Relancer le lendemain suffit en général.
- **Inactivité** : GitHub peut suspendre les tâches planifiées d'un dépôt public après
  60 jours sans activité. Le commit quotidien compte en principe comme une activité.
  Sinon, un clic dans l'onglet Actions réactive la tâche.

## Aide : si le dossier `.github` n'est pas parti

Dans le dépôt, « Add file » › « Create new file ». Tapez comme nom
`.github/workflows/mise-a-jour.yml` (chaque « / » crée un dossier), collez le contenu du
fichier correspondant ouvert avec le Bloc-notes ou TextEdit, puis « Commit changes ».
Recommencez avec `.github/workflows/backtest.yml`.

Outil d'aide à la décision, ce n'est pas un conseil en investissement.
