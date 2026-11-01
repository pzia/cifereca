# P0 #3909 — fenêtre de lecture du journal pour réparer un stock staging déjà publié

## Défaut corrigé

`canonical_staging_stock._record()` vérifie un `Placement` et un `OriginLink`
fournisseur exacts avant de reprendre une ancienne publication dont le chemin
physique pointe encore sur `.canonical-delivery`. Il cherchait toutefois le
`canonical.completed-delivery.v1` parmi les **32 premières lignes**, sans
`ORDER BY`, d'un ensemble comportant aussi les reçus legacy du même livre,
compte et Artifact. Si le seul reçu Core était au-delà de ce plafond, la
réparation échouait sous `canonical_stock_receipt_missing_or_ambiguous`.
Les octets étaient préservés, mais le livre restait dans le staging et les
nouvelles Sync n'amélioraient pas le stock.

## Règle

- Filtrer d'abord `acquisition_id LIKE 'canonical-intake:%'`, c'est-à-dire
  exclusivement le namespace de checkpoints construit par le Core.
  Les opérations legacy, même `complete` avec le même SHA, ne sont jamais
  admissibles à la réparation.
- Conserver la preuve stricte existante de `_record` :
  `SourceKey` et coordonnées de compte, `_stage_id(source,offer)`,
  JSON sans clés dupliquées, chemin exact, SHA actuel du Placement,
  `OriginLink` fournisseur exact et reçu Core.
- Lire un ensemble borné de **513 candidats** triés par identifiant.
  En présence de plus de 512 candidats ou d'une preuve exacte ambiguë,
  échouer fermé : ni suppression de staging, ni nouveau GET, ni association
  inventée. Aucune sélection arbitraire du « premier » reçu.
- La promotion physique reprend ensuite la procédure atomique habituelle
  (copie/flush, commit SQLite de la relocalisation, puis nettoyage de staging).

## Tests

Une vraie base v14 et un fournisseur synthétique reproduisent un placement
historique déjà publié dans le staging. Insertion de 48 reçus parasites
(legacy puis faux namespace Core) : la réparation retrouve le reçu
Core exact, garde le Placement/OriginLink et passe en dossier final sans
un second GET. Le dépassement 512+ candidats est refusé sans modifier les
octets ni la base. Aucun service externe n'est contacté.

Cette amélioration ne constitue **pas** une attribution de provenance aux
six EPUB Numilog observés : leur source et leurs reçus doivent encore
être qualifiés en recette installée.
