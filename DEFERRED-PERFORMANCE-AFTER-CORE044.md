# Performance — suites après Core044

À mesurer avant implémentation :

1. scan filesystem avec transaction DB batch par scan ;
2. projection Bibliothèque cache/génération si les grandes bibliothèques le justifient ;
3. réutilisation SHA-256 contrôlée sur tous les leases à partir de signature stat fiable ;
4. rollup/rétention des événements d'actions si leur volume devient significatif ;
5. FTS5 uniquement si la recherche locale Python devient un hotspot mesuré ;
6. métriques p95 sur `storage`, application de batch UI et providers réels/lents.