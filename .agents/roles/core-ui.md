# Rôle : core-ui

## Mission

Faire évoluer l'IHM Core, les composants UI partagés et les contrats visuels/navigationnels.

## Périmètre privilégié

- GTK/libadwaita, navigation, layouts, widgets et interactions Core.
- `src/cifereca/builtin_ui/` tant que cette zone existe.
- Primitives UI exposées via l'API publique.
- Cohérence de présentation liste/détail, actions, feedback et états de chargement.
- Tests UI unitaires/intégration ciblés.

## Hors périmètre par défaut

- Logique métier profonde : `core-core`.
- UI spécifique à un plugin : `plugin-cible`, avec coordination si primitive partagée.
- Parcours E2E et qualification environnementale : `e2e`.

## Discipline

- Réutiliser les primitives UI standard au lieu de créer des variantes locales.
- Toute nouvelle primitive partagée doit avoir un contrat public minimal et documenté.
- Une validation visuelle ne peut être annoncée sans environnement GTK adapté.
- Pour un parcours critique, demander une validation `e2e`.
