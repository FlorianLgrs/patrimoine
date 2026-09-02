---
description: Reconstruit l'onglet Graphique du Google Sheets (évolution mensuelle du PEA : versements cumulés + valeur des titres) et crée/met à jour le graphique en courbes.
---

Lance `python3 build_graphique.py` depuis la racine du projet. Le script est idempotent : il réécrit le tableau mensuel (fins de mois depuis mars 2020 jusqu'au mois en cours), recalcule les quantités détenues et les cours GOOGLEFINANCE, puis met à jour les plages du graphique existant (ou le crée si absent). Présente le rapport à l'utilisateur (plage écrite, dernière ligne versements/valeur, compléments Yahoo éventuels, vérifications).

Si le script sort avec le code 2 et mentionne un refresh token expiré, propose à l'utilisateur de lancer `python3 authenticate.py` (flux OAuth navigateur), puis relance.

Si le script sort avec le code 1 (« Vérification: PROBLÈMES »), montre les écarts à l'utilisateur et relis les plages en question (`UNFORMATTED_VALUE` + `FORMULA`) avant toute nouvelle écriture — ne force jamais une réécriture pour « réparer ».

À relancer chaque mois (ou après un nouvel achat sur une nouvelle ligne Portfolio) pour étendre le tableau : les cours passés restent live via GOOGLEFINANCE, seules les nouvelles lignes/mois sont ajoutés.

Après l'exécution, relis la dernière ligne du tableau (`Graphique!A{dernière}:C{dernière}`) et signale tout écart entre la valeur PEA et la somme « Valeur actuelle » du Portfolio (tolérance ~2 %, cours du jour vs prix actuel).
