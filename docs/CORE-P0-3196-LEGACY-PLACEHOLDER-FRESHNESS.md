# Core P0 #3196 — horodatage d'une projection physique legacy

## Invariant

`Storage.backup_put()` maintient temporairement la projection de
compatibilité Kobo 0.7. Son mirroring peut créer un `Placement`
**`state='unverified'`** à partir d'un chemin et d'un SHA seulement
*déclarés par le fournisseur* : ce n'est ni une lecture du fichier, ni un
`OriginLink(provider)`, ni une preuve de présence locale.

Avant cette tranche, chaque replay de ce même reçu rafraîchissait
`placements.updated_at` alors que `last_seen_at` restait `NULL`. Sur
les inventaires volumineux et les livres archivés, cela pouvait faire passer
un placeholder historique pour une référence physique récemment actualisée.

La transaction de compatibilité continue de mettre à jour
`backup_items` et les champs métiers legacy préexistants. Seul
`placements.updated_at` est désormais conservé lorsque **les trois
faits physiques revendiqués restent identiques** :
`artifact_id` (SHA revendiqué), `provider_id`, `source_item_id`.
Si l'un change, le placeholder legacy peut être remis à jour mais reste
`unverified`, sans `last_seen_at` ni origine canonique. L'état d'un
Placement réellement vérifié reste protégé par le garde déjà existant.

## Validation

`tests/test_core_p0_3196_legacy_placement_noop.py` utilise SQLite réel,
un chemin absent et des horodatages déterministes. Il couvre un replay
identique, une correction du titre descriptif, un changement de SHA revendiqué,
un second replay et l'absence de SHA. Aucune nouvelle `SourceReference`
ou `OriginLink` n'est fabriquée, aucun téléchargement n'est fait.

Cette mesure **ne retire pas** `backup_items` du chemin d'écriture : la
migration 0.7/0.8 et la conversion vers SourceReference/Artifact/OriginLink/
Placement restent à achever sous #3196/#3171. Aucun droit ni preuve
fournisseur nouveau n'est déduit de ce simple horodatage.
