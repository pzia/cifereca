# P0 #3196 / W1 — emprunteur historique non exact : archive seule

La ligne `backup_items` Kobo v0.7 est conservée pour le diagnostic et
la reprise ; sa clé n'inclut ni l'identité ni le borrower. Un
`account_id` historique peut être `" "`, paddé, ou contenir CR, LF ou
NUL, tout en portant `complete`, SHA et chemin. Cette chaîne n'est pas
un `ProviderRightsScope` exact et ne doit pas devenir droit, acquisition
ou Placement générique par le miroir W1.

`Storage.backup_put` inspecte **l'enregistrement fusionné** dans sa
transaction d'UPSERT. Pour Kobo seulement, un borrower non chaîne,
à blancs périphériques ou contenant CR/LF/NUL ne peut plus alimenter
`entitlements/acquisition_ledger/artifacts/placements/catalog_entries`.
L'archive 0.7 et ses valeurs brutes sont conservées ; la garde de
collision avec les droits/reçus canoniques reste active et atomique.

Le compte Kobo primaire **`account_id=""`** est valide, ainsi que
les identifiants non vides exacts. Aucun changement pour les autres
fournisseurs. Ni droit fournisseur, SourceReference, OriginLink, GET,
copie, migration ou effacement n'est inféré.

Recette synthétique SQLite :
`tests/test_core_p0_3196_w1_unscoped_borrower_archive.py`.
Le chemin W1 lié aux sources actives et le cutover W2/D1–D4 restent
à supprimer seulement avec migration prouvée, sans clôture de #3196.
