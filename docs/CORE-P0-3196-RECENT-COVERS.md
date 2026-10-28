# Core #3196 — retrait du lecteur legacy des couvertures récentes

Dans `core/library_projection.py::recent_acquisitions`, les couvertures
des placements locaux ne consultent plus `Storage.backup_list("kobo")`.
Auparavant une ligne `backup_items` identifiée seulement par `item_id`
pouvait être utilisée même sans binding de credential vérifiable
(`record_identity=None`), et alimenter la vue d'une autre identité.

La projection présente désormais les couvertures déjà inscrites dans le
catalogue local, le catalogue public et les métadonnées d'entitlement
canonique ainsi que les prêts observés. Aucune déduction de droit ni de
provenance fournisseur ne découle de la couverture affichée. Les lignes
0.7 historiques restent en base et conservent leur rôle de migration et
de diagnostic sans autorité sur une vue de placement actuel.

## Recette CI

- `tests/test_catalog_083.py::test_recent_acquisitions_reads_cover_sources_once_not_per_placement` :
  0 lecture legacy et 1 lecture groupée des entitlements.
- `tests/test_catalog_083.py::test_recent_covers_ignore_foreign_or_unscoped_legacy_backup_even_with_matching_item` :
  même `item_id`, ancien compte étranger, aucune couverture historique
  promue ; une couverture d'entitlement explicite du profil reste présentée.
- Les tests Core/Plugins, installation et packaging existants doivent rester
  verts avant fusion.

## Restant #3196

Cette tranche ne retire pas `Storage.backup_put/get/list/prune`, ni les
écritures ou lecteurs opérationnels de `kobo_backup`. La migration versionnée
0.7/0.8, les récépissés et la reprise crash-safe demeurent à achever ;
pas de suppression de table ni fermeture de l'issue sur cette PR.
