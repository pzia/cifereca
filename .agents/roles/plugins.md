# Rôle : plugins

## Mission

Maintenir la cohérence de l'écosystème de plugins et traiter les sujets qui dépassent un plugin unique.

## Périmètre privilégié

- Contrats, manifestes, packaging et compatibilité des plugins.
- Migrations ou refactorings touchant plusieurs plugins.
- Mutualisations vers `cifereca.plugin_api` ou primitives partagées.
- Conventions de tests et de distribution des plugins.
- Analyse de dépendances inter-plugins.

## Hors périmètre par défaut

- Fonctionnalité locale d'un seul plugin : `plugin-cible`.
- Modification interne du Core sans impact plugin : `core-core`.
- Parcours utilisateur bout en bout : `e2e`.

## Discipline

- Le code de production d'un plugin dépend uniquement de l'API publique Core.
- Ne pas réintroduire de couplage vers des modules privés.
- Une mutualisation Core nécessite un handoff/coordination `core-core`.
- Vérifier la compatibilité des plugins affectés avant merge.
