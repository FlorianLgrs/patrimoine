---
description: Importe le CSV d'opérations bourse le plus récent dans l'onglet Transactions Google Sheets (avec dédoublonnage).
---

Lance `python3 import_bourse.py` depuis la racine du projet (détection automatique du CSV le plus récent). Présente ensuite le rapport à l'utilisateur (lignes ajoutées / ignorées / inconnues / signalées).

Si le script sort avec le code 2 et mentionne un refresh token expiré, propose à l'utilisateur de lancer `python3 authenticate.py` (flux OAuth navigateur), puis relance l'import.

Si le rapport contient des lignes "INCONNUES", suggère d'éditer `mapping.json` pour ajouter le symbole ou le type manquant, puis relance l'import.
