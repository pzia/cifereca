# P0 #3196 / W1 — Rapprochements Kobo sans preuve physique

Un statut historique `reconcile_conflict` ou `reconcile_required` ne vaut ni droit ni acquisition.
L'ancien `Storage.backup_put` faisait pourtant une projection complète
vers `entitlements`, `acquisition_ledger`, `artifacts`, `placements`
et `catalog_entries`, même lorsque le conflit n'avait **aucun SHA ou taille**
utilisable par le rapprochement manuel W4.

La tranche coupe ce miroir pour les `reconcile_conflict` et `reconcile_required` dont le
SHA-256 n'est pas exactement 64 caractères hexadécimaux ou dont la taille
historique est absente, non entière ou non positive. Le code conserve :
- la ligne `backup_items` et ses éventuels chemins candidats pour revue ;
- la vérification de collision de propriétaire canonique sous la même
  transaction SQL ;
- les tables canoniques préexistantes, sans remise à zéro ;
- le miroir temporaire des `reconcile_conflict` et `reconcile_required`
  disposant de SHA/taille exploitables, indispensables au W4 existant ;
- tous les chemins de compatibilité des autres fournisseurs.

La coupure n'affirme pas qu'un SHA historique est une attestation du fichier :
**même avec SHA/taille, la validation physique W4 demeure obligatoire**.
Aucun droit Kobo, OriginLink, GET, suppression de fichier ou migration de compte
n'est créé. Cette tranche ne règle pas la coupure finale de W1/W2/W4.

Réception : `tests/test_core_p0_3196_w1_passive_kobo_checkpoint_cutover.py`
inclut refus des empreintes ou tailles invalides pour les **deux** statuts,
non-mutation SQLite, collision de propriétaire, préservation d'un droit/reçu
indépendants et conservation du miroir pour un W4 encore réparable.
