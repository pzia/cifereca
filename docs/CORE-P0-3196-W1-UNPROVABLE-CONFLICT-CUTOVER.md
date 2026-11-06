# P0 #3196 / W1 — Rapprochements Kobo sans preuve physique

Un statut historique `reconcile_conflict` ou `reconcile_required` ne vaut ni droit ni acquisition.
L'ancien `Storage.backup_put` faisait pourtant une projection complète
vers `entitlements`, `acquisition_ledger`, `artifacts`, `placements`
et `catalog_entries`, même lorsque le conflit n'avait **aucun SHA ou taille**
utilisable par le rapprochement manuel W4.

La tranche coupe ce miroir pour les `reconcile_conflict` **et**
`reconcile_required` sans preuve exploitable **et sans connexion fournisseur
Kobo exacte du profil propriétaire**. Dans ces deux cas, le SHA-256 n'est pas
exactement 64 caractères hexadécimaux ou la taille historique est absente,
non entière ou non positive. Pour un compte Kobo effectivement lié, le manifeste
OWN peut constituer une observation de droit distincte de l'attestation du fichier,
y compris quand plusieurs fichiers candidats conduisent à `reconcile_conflict`.
Tant que W2 ne projette pas ces droits de manière typée, le miroir historique
reste indispensable à leur découverte, même avec un fichier non confirmé. Le code conserve :
- la ligne `backup_items` et ses éventuels chemins candidats pour revue ;
- la vérification de collision de propriétaire canonique sous la même
  transaction SQL ;
- les tables canoniques préexistantes, sans remise à zéro ;
- le miroir temporaire des `reconcile_conflict` et `reconcile_required`
  disposant de SHA/taille exploitables, indispensables au W4 existant ;
- les découvertes `reconcile_required` d'un credential Kobo toujours
  explicitement rattaché à la même identité, même si le fichier n'a pas
  d'empreinte : **leur droit fournisseur doit être migré avec W2** ;
- tous les chemins de compatibilité des autres fournisseurs.

La coupure n'affirme pas qu'un SHA historique est une attestation du fichier :
**même avec SHA/taille, la validation physique W4 demeure obligatoire**.
Aucun droit Kobo, OriginLink, GET, suppression de fichier ou migration de compte
n'est créé. Cette tranche ne règle pas la coupure finale de W1/W2/W4.

Réception : `tests/test_core_p0_3196_w1_passive_kobo_checkpoint_cutover.py`
inclut refus des empreintes ou tailles invalides pour les **deux** statuts,
non-mutation SQLite, collision de propriétaire, préservation d'un droit/reçu
indépendants, découverte OWN sur compte actif et maintien du miroir W4
lorsqu'il est encore réparable.

**Correctif de compatibilité W2 :** un manifeste Kobo OWN courant est une
observation positive du droit de son compte, même s'il laisse deux fichiers
locaux candidats et aucun SHA fiable. Ne jamais supprimer cette découverte
lors de la classification `reconcile_conflict`. Le miroir W1 persiste
temporairement pour une *connexion exacte* ; seule la source sans owner
connecté est archive-only. Voir
`tests/test_kobo_backup_reconcile_prune.py::test_multiple_local_candidates_become_conflict_without_deletion`
et le test paramétré de périmètre dans
`tests/test_core_p0_3196_w1_passive_kobo_checkpoint_cutover.py`.
Le droit ne vaut pas attestation d'Artifact B.
