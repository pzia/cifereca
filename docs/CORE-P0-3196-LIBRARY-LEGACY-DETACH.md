# Core P0 #3196 — la bibliothèque ne lit plus `backup_items`

`LibraryService.library_entries()` utilisait encore `backup_list("kobo")` pour enrichir les titres, auteurs, formats et couvertures de droits pourtant déjà présents dans `entitlements`. La clé historique `(provider,credential,item)` ne porte ni l'identité ni l'emprunteur : cette jointure descriptive peut injecter le titre ou la couverture privée d'un autre compte dans la bibliothèque courante.

La projection UI est désormais uniquement alimentée par les métadonnées **de l'Entitlement canonique**, les prêts typés contextualisés et les métadonnées de catalogue/Placement pertinentes. Les anciens `backup_put` continuent, à titre transitoire, de projeter leurs titres/couvertures dans `entitlements.metadata` : les informations déjà migrées restent donc disponibles sans deuxième lecture du legacy. Les snapshots de fournisseurs partiels n'impliquent pas de droit absent et une ligne `backup_items` non miroir ne crée pas de livre distant.

## Preuves et limites

`tests/test_core_p0_3196_library_view_legacy_detach.py` couvre :
- une ligne Alice héritée partageant même credential et édition avec le droit canonique Bob, dont les métadonnées privées ne doivent pas fuiter ;
- l'enrichissement d'un ancien achat par son `entitlement` miroir sans appel `backup_list` ;
- une ligne purement legacy sans droit qui reste invisible comme possession.

Les tests interdisent explicitement toute lecture de `backup_items` par `library_entries` et s'exécutent hors ligne sur SQLite réel. Le retrait des autres lecteurs/writers Kobo, la migration 0.7/0.8 et les parcours réels fournisseurs restent dans #3196/#3171 ; aucune suppression ni P2.
