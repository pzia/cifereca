# #3171 — projection descriptive après publication canonique

Une acquisition validée publie d'abord en **une seule transaction**
`Artifact / SourceReference / Placement / OriginLink`. Ensuite seulement,
Core invoque `LocalCatalog.index_path()` sur le chemin exact publié,
avec le profil attendu et **sans** `provider_id` ou `item_id` induits
d'un nom de dossier. Le catalogue reste une projection descriptive, pas
une nouvelle autorité de droits ni de provenance. Aucun scan intégral
n'est lancé pour rendre le livre visible après Sync.

L'indexation peut échouer pour une cause physique concurrente, un catalogue
corrompu ou un arrêt après la transaction canonique. Un tel incident se
journalise sans révoquer l'OriginLink déjà validé ni convertir le résultat
métier en échec de livraison : la Sync suivante ne doit pas relancer de
GET pour une présence exacte et fraîche. Une indexation manuelle/scan
complet ultérieur peut réparer la vue.

Tests SQLite : nouvelle acquisition rend une seule entrée de catalogue
locale sans scan ; interruption contrôlée de l'indexation après publication,
préservation des quatre faits canoniques, seconde Sync `present_attested`
sans GET ni duplication. Aucun changement fournisseur, `backup_items`
ou P2. La validation des octets, du profil et du SHA reste celle de
`canonical_publication` et `LocalCatalog`, sans autorité nouvelle.
