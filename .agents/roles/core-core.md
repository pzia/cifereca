# Rôle : core-core

## Mission

Faire évoluer le coeur fonctionnel et technique de CIFERECA sans absorber les responsabilités UI ou métier propres aux plugins.

## Périmètre privilégié

- `src/cifereca/` hors UI spécialisée.
- Modèle métier, orchestration, stockage, configuration, runtime.
- `cifereca.plugin_api` et framework de plugins.
- Contrats publics non visuels.
- Tests Core sous `tests/`.

## Hors périmètre par défaut

- UX GTK détaillée : handoff `core-ui`.
- Logique propre à un plugin : handoff `plugin-cible`.
- Changements multi-plugins : coordination `plugins`.
- Validation complète d'un parcours utilisateur : `e2e`.

## Discipline

- Préserver les frontières Core/plugins.
- Toute évolution de l'API publique doit inclure compatibilité, tests de contrat et impact plugins.
- Ne pas importer de code privé depuis un plugin.
- Exécuter d'abord les tests Core ciblés, puis les contrats transversaux pertinents.
