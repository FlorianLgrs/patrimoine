# AGENTS.md

Suivi de portefeuille bourse personnel : scripts Python qui synchronisent un export CSV du courtier vers un Google Sheets. Pas de build, pas de tests, pas de lint — la vérification se fait en relisant les plages écrites via l'API et en recalculant les valeurs attendues depuis les données.

## Accès Google Sheets

- Le « fichier Excel » est le Google Sheets `1zyJzpGZRvq4CAw2jSw3zeswZIavRmV5kMh5JNBjGPbc` (ID dans `config.json`). Onglets : `Portfolio` (principal), `Répartition Sectorielle`, `Transactions`, `Versement`, `Graphique`, `XPath` (masqué).
- Auth OAuth : token dans `~/.config/patrimoine/token.json` (hors repo, chmod 600), access token rafraîchi automatiquement par les scripts ; client secret dans `~/.config/patrimoine/client_secret.json`.
- App OAuth en statut « Testing » : le refresh token expire tous les 7 jours. Si un script sort avec le code 2 (`invalid_grant`), faire lancer `python3 authenticate.py` (flux navigateur) puis relancer.
- L'outil MCP `google-sheets` d'opencode peut échouer avec « caller does not have permission » ; la méthode fiable est curl avec le bearer token de `token.json` contre `https://sheets.googleapis.com/v4/spreadsheets/<id>/...` (voir la fonction `http()` de `import_bourse.py`).
- Locale de la feuille : **français**. Formules avec `;` comme séparateurs et **virgule décimale** (`34,171` = 34.171), dates `jj/mm/aaaa` en colonne A. Écrire avec `valueInputOption=USER_ENTERED`.

## Commandes

- `python3 import_bourse.py [--dry-run] [csv]` : importe le CSV le plus récent (`HistoriqueOperationsBourse_*.csv`, encodage cp1252, séparateur `;`) dans `Transactions` avec dédoublonnage. Commande opencode : `/import-bourse` (`.opencode/command/import-bourse.md`).
- `python3 import_versements.py [--dry-run] [csv]` : importe les lignes « versement » du CSV compte de règlement (en-tête `Date opération;Date valeur;libellé;Débit;Crédit`, cp1252, `;`) dans `Versement` (date valeur + crédit, dédoublonnage) et vérifie le delta de `Portfolio!G42`. Commande opencode : `/import-versements` (`.opencode/command/import-versements.md`).
- `python3 build_graphique.py [--dry-run]` : reconstruit l'onglet `Graphique` (évolution mensuelle du PEA : versements cumulés + valeur des titres) et crée/met à jour le graphique en courbes. Idempotent, à relancer pour étendre aux mois suivants. Commande opencode : `/graphique` (`.opencode/command/graphique.md`).
- Si le rapport liste des « INCONNUES » : compléter `mapping.json` (libellé CSV → symbole, opération → type) puis relancer.
- Après toute écriture : relire les plages (`UNFORMATTED_VALUE` + `FORMULA`) et recalculer les valeurs attendues depuis `Transactions`.

## Structure de la feuille Portfolio

- Lignes 2-15 = 14 positions. B = symbole Google (`EPA:EWLD`), C = symbole utilisé dans `Transactions` (`EPA.EWLD`, `FP.PA`…), D = secteur.
- Blocs par année (Pondération / Performance / Perf à aujourd'hui) : O:Q = 2020, R:T = 2021, U:W = 2022, X:Z = 2023, AA:AC = 2024, AD:AF = 2025, AG:AI = 2026. Ligne 38 = agrégats `SUMPRODUCT(poids;perf)` par paire.
- `Transactions` : A date, B année (formule), C type, D quantité, E symbole, F prix, G frais, H total (formule si Achat, sinon valeur).
- `Versement` : A = date (**date valeur** du CSV compte de règlement, pas la date opération), B = montant du crédit. `Portfolio!G42 = sum(Versement!B2:B100)`. Les deux formats de CSV partagent le même préfixe de nom : en-tête `libellé;Opération;…` = opérations (→ `import_bourse.py`), en-tête `Date opération;…` = compte de règlement (→ `import_versements.py`).

## Onglet Graphique (généré par `build_graphique.py`)

- Onglet `Graphique` (sheetId 27692790). Graphique en courbes ancré en A1 (1200×500, séries B et C contre A, axes « Mois » / « Montant (€) ») ; le script repère le chart existant via `sheets.charts` et met à jour ses plages (`updateChartSpec`) au lieu de le recréer. Tableau sous le graphique : en-têtes ligne 28, données lignes 29+, une ligne par fin de mois depuis 31/03/2020 (premier achat) jusqu'au mois en cours.
- A = fin de mois (format `mmmm yyyy`), B = `SUMIF(Versement…;"<="&$A{r};…)` versements cumulés, C = `SUMPRODUCT($D{r}:$N{r};P{r}:Z{r})` valeur des titres.
- D..N = quantités détenues par symbole : `SUMIFS(Transactions…;"Achat";…;"<="&$A{r};…;{col}$28)` — mêmes règles que les colonnes Performance (filtre `Achat` obligatoire, plages `$2:$1000`). En-têtes D28..N28 = symboles `Transactions` (col. C du Portfolio).
- P..Z = cours de clôture GOOGLEFINANCE : `LET(g;GOOGLEFINANCE({col}$28;"close";MIN($A{r};TODAY())-7;MIN($A{r};TODAY()));IFERROR(INDEX(g;ROWS(g);2);0))` — fenêtre 7 jours (fins de mois non cotés), `MIN(;TODAY())` pour le mois en cours. En-têtes P28..Z28 = symboles Google (col. B du Portfolio, `FP.PA` → `EPA:TTE`).
- Symboles dérivés des lignes Portfolio avec Actions > 0 (11 en 09/2026) ; une nouvelle ligne achetée nécessite de relancer le script (elle ajoute colonnes + mois).
- GOOGLEFINANCE couvre l'historique pré-rachat de TTE (75,39 € au 28/02/2021) ; si une cellule de cours restait à 0 avec quantité > 0, le script la complète en dur via Yahoo (`yahoo_close`) et le signale.
- **Piège corrigé le 02/09/2026** : les dates `Transactions!A2:A17` étaient du texte (format de cellule `TEXT`) → tout critère `"<="&date` les excluaient silencieusement (477 EWLD + 58 PLEM + tous les achats OR/BN/SAN/CS/FP ignorés). Corrigé en format `DATE dd/mm/yyyy`. Si des quantités semblent fausses, vérifier que la colonne A ne contient pas de dates-texte.

## Sémantique des formules (ne pas « simplifier »)

- `Performance YYYY` = `(clôture_31/12/YYYY * somme des quantités achetées dans l'année) / somme des totaux payés pour ces achats - 1`, prix en dur dans la formule (virgule décimale).
- **Filtre obligatoire `Transactions!$C$2:$C$1000;"Achat"`** dans countifs/sumifs des colonnes Performance et Perf à aujourd'hui : les lignes « OST Coupon »/« Dividende » portent quantité = nombre de parts et un petit montant ; sans filtre les perfs explosent (+8000 % observés). La Pondération, elle, compte tous les flux sans filtre de type — voulu.
- Plages `Transactions!$2:$1000` : les données dépassent la ligne 101 (2024 → ligne 112, 2025 → 135, 2026 → 159+).
- `Performance 2026` (AH) volontairement vide : à remplir en janvier 2027 avec les clôtures du 31/12/2026.
- Anomalie connue, non corrigée : Y2 (Perf 2023 EWLD) référence « 2022 » dans un des sumifs du dénominateur.

## Prix historiques (Yahoo Finance)

- Le nombre en dur dans les formules = clôture Yahoo exacte du 31/12 de l'année (vérifié : EWLD 34,171 = clôture du 31/12/2024).
- Endpoint : `https://query1.finance.yahoo.com/v8/finance/chart/<sym>?period1=<epoch>&period2=<epoch>&interval=1d` avec header `User-Agent` requis. Symboles `X.PA` (Google `EPA:X` → Yahoo `X.PA`).
- **`FP.PA` (TotalEnergies, symbole utilisé dans la feuille) est délisté chez Yahoo → utiliser `TTE.PA`** pour les données de marché.

## Confidentialité

- `HistoriqueOperationsBourse_*.csv` et `opencode.json` sont gitignorés (données courtier + secret OAuth). Ne jamais les committer ni recopier leur contenu dans un autre fichier suivi.
