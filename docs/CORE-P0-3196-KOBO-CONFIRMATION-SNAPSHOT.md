# Core P0 #3196 — snapshot SQLite cohérent à la confirmation Kobo legacy

## Risque corrigé

Le parcours d'acquisition explicitement confirmé de Kobo (`source_materialization._evidence`) utilise provisoirement `backup_items` **comme garde de cohérence et d'interruption**, et non comme preuve positive de droit ou d'OriginLink. Il calcule un digest privé des preuves de six tables (`backup_items`, `entitlements`, `acquisition_ledger`, `provider_connections`, `placements`, `catalog_entries`) puis compare le digest au moment de la confirmation.

Le mutex Python de `Storage` ne protège pas contre une écriture d'un **autre processus**. Avant correction, chaque `SELECT` était une lecture SQLite indépendante : un changement de propriétaire, de reçu ou de placement pendant les six lectures pouvait produire une empreinte de générations contradictoires.

## Invariant livré

Sous le mutex existant, les six lectures utilisent désormais un **même snapshot SQLite via `SAVEPOINT kobo_materialization_evidence`**, libéré dans un `finally` même si un lot dépasse sa limite de 10 000 lignes. Un `SAVEPOINT` est compatible avec la connexion SQLite réutilisée par thread et avec une transaction externe éventuelle ; la méthode ne ferme pas cette connexion et ne commite pas de transaction d'appelant.

Cela ne modifie pas l'autorisation : le transport reste sous le verrou interprocessus d'acquisition et exige toujours une confirmation explicite, un nouveau manifeste, le scope exact et la comparaison du digest. Aucune table n'est modifiée par ce contrôle, et la projection v0.7 ne devient pas une SourceReference canonique.

## Réception

`tests/test_source_materialization_confirmation.py` ajoute :

- un test WAL avec **deux connexions SQLite réelles**, modifiant une connexion fournisseur entre les lectures : l'empreinte pendant l'opération correspond à l'ancien snapshot cohérent, la lecture suivante voit le changement ;
- une erreur budgétaire injectée **au sein** du snapshot, suivie d'une vérification de la libération du `SAVEPOINT` et d'une nouvelle lecture possible.

Limites : cette tranche ne retire pas les écrivains `backup_put`, n'effectue pas la migration 0.7/0.8, ne certifie pas les droits depuis `backup_items` et ne constitue pas la recette réelle Ubuntu/fournisseur requise pour fermer #3196.
