---
description: Importe les versements du CSV compte de règlement le plus récent dans l'onglet Versement Google Sheets (avec dédoublonnage).
---

Lance `python3 import_versements.py` depuis la racine du projet (détection automatique du CSV au format compte de règlement — en-tête « Date opération » — le plus récent). Présente ensuite le rapport à l'utilisateur (lignes ajoutées / déjà présentes / anomalies, delta de `Portfolio!G42`).

Si le script sort avec le code 2 et mentionne un refresh token expiré, propose à l'utilisateur de lancer `python3 authenticate.py` (flux OAuth navigateur), puis relance l'import.

Si le rapport contient des anomalies (versement en débit, date illisible), montre-les à l'utilisateur et fais vérifier le CSV source — ne force jamais l'écriture.

Après une écriture, relis la plage écrite et `Portfolio!G42` pour confirmer (le script le fait déjà, mais signale tout écart).
