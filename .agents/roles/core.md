# Rôle : core

## Mission
Traiter le Core applicatif et le Core UI, dans cet ordre de priorité.

## Ordre d'itération
1. Traiter d'abord les sujets Core non-UI : métier, services, stockage, configuration, API publique, framework de plugins.
2. Ensuite traiter les sujets Core UI : GTK/libadwaita, navigation, composants UI et contrats UI publics.

## Périmètre
- `src/cifereca/**`.
- Tests centraux affirmant des invariants Core/Core UI.
- API publique et framework de plugins côté Core.

## Frontière stricte
Le rôle `core` ne modifie pas les plugins fonctionnels pour terminer une évolution Core. Si une adaptation plugin est nécessaire, créer ou enrichir une issue `plugins`.
