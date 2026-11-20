
# Backtest de l'algorithme

Le dossier `backtest/` teste 36 combinaisons de règles sur les actions ayant fait partie
du S&P 500 depuis 2020 :

- 3 familles de règles : tendance MM20/MM200, momentum 12 mois, ou les deux combinées ;
- avec ou sans confirmation par figure de chandelier (marteau, avalement haussier) ;
- avec ou sans filtre « pas d'achat juste avant une publication de résultats » ;
- 3 largeurs de stop suiveur : 2,5, 3,5 ou 5 fois l'ATR, c'est-à-dire la variation
  quotidienne moyenne de l'action.

Toutes partagent la même gestion du risque : 10 000 €, 10 lignes maximum, 1 % du capital
risqué par ligne, pas de nouvel achat quand le S&P 500 est sous sa MM200, frais inclus.

## Étape 1 : mise au point (2021-2024)

1. Envoyez sur GitHub le dossier `backtest/` et le fichier `.github/workflows/backtest.yml`
   (« Add file » puis « Upload files », en gardant les dossiers).
2. Allez dans l'onglet Actions › « Backtest » › « Run workflow », choisissez
   `developpement`, puis validez. Comptez 15 à 30 minutes.
3. Le rapport s'ouvre à l'adresse `https://votre-nom.github.io/signaux/backtest/rapport_developpement.html`.
4. Envoyez-moi le contenu de `backtest/resultats_developpement.json` (ou une capture du
   rapport) : on choisit ensemble la configuration à retenir.

## Étape 2 : validation (2025-2026), une seule fois

1. Créez `backtest/config_retenue.json` avec la configuration choisie, par exemple :
   `{"famille": "momentum", "chandeliers": false, "resultats": true, "atr_k": 3.5}`
2. Lancez « Backtest » avec `validation`.
3. Rapport : `.../backtest/rapport_validation.html`

Le programme garde la trace de chaque validation lancée et affiche un avertissement si
on la relance avec d'autres réglages. Retoucher les règles après avoir vu 2025-2026
fausserait le test.

Pour vérifier le programme sans Internet : `python backtest/backtest.py --demo` (cours fictifs).
