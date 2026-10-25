# P1 #3119 / #3122 — consumption des snapshots par la synchronisation explicite

## Portée

`synchronize_account_source(account)` adopte désormais le résultat atomique
`loan_inventory_snapshot(account)` si, et seulement si, le provider l'implémente.
Un plugin v1 sans cette méthode conserve exactement son chemin historique
`_loans_for_account(force=True)` ; aucun second GET n'est effectué.

L'API `read_loan_inventory` vérifie l'enveloppe typée et la portée de
`identity/provider/credential/borrower`. Le consommateur Core reprend sa
propre validation **après I/O** : identité existante, compte toujours
enregistré par identité objet, epoch, session toujours enregistrée et connectée,
source toujours activée, puis annulation avant toute publication.

Les snapshots `failed`/`cancelled` sont rejetés sans écriture de droits,
ni fallback permissif vers `loans()`. Un snapshot `partial` garde ses
observations positives et produit un statut de sync partiel **sans relire**
`last_loan_errors`, qui peut avoir été écrasé par une lecture concurrente.
Le cache de prêts actualisé contient les mêmes lignes positives scellées
(`raw={}`) que le lecteur historique, mais n'est écrit qu'après validation
de la portée Core.

## Frontières de sécurité

- Aucun retour, emprunt, ADEPT, ACSM, téléchargement ou mutation distante.
- La lecture positive ne déclenche aucune suppression : toujours
  `reconcile_missing=False` et `realize_expired=False`.
- Aucun statut « success » ni branche « complete » ne confère à lui seul
  une preuve négative de disparition. Ni BnF HTML vide, ni Paris
  DILICOM/SYRACUSE non exhaustifs ne ferment un droit.
- Aucun payload fournisseur privé ni URL n'est copié dans les diagnostics.
- Le flux d'ouverture automatique et les autres lecteurs legacy ne sont
  **pas** migrés par ce lot ; leurs contrôles #2975 restent inchangés.

## Tests hors ligne

`tests/test_core_p1_3119_3122_explicit_snapshot_sync.py` : un seul appel
typé et aucun appel legacy en doublon, droits positifs et cache, inventaire
partiel vide conservant les droits, états failed/cancelled, portée étrangère,
remplacement de session/compte, changement d'epoch, désactivation, annulation
entre GET et publication. Les cas legacy demeurent dans
`test_core_p0_2975_manual_source_loan_parity.py`.

Restant #3122 : intégration des autres consommateurs sous contrat epoch,
tests GTK, matrice de conformité et recettes fournisseurs/rollback.
