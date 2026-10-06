# Cifereca Core 0.9.44-dev.1

Release de performance interne, sans changement de Plugin API ni de Manifest.

## Performance

- suppression du N+1 du miroir catalogue ;
- batch lookup/upsert SQLite ;
- connexions SQLite réutilisées par thread ;
- nouveaux index de projection (schéma 12) ;
- indexation ciblée sans scan complet catalogue ;
- recherche provider sur pool partagé avec supersession/cancellation réactive ;
- application incrémentale des batches GTK lorsque la liste ne fait que croître.

## Compatibilité

- Plugin API : `1.4.0` inchangée ;
- Manifest : `1` inchangé ;
- contrats brokers/catégories : inchangés ;
- schéma : `12`, migration additive d'index uniquement.

## Mesure

Benchmark synthétique 5 000 entrées + 100 résultats miroir : ~1,84–1,99 s sur Core043 contre ~2,1 ms sur Core044 dans l'environnement de recette.
