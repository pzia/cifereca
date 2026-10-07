# Rôle : plugins

## Mission
Traiter tous les plugins fonctionnels, qu'il s'agisse d'un plugin unique ou de sujets transversaux.

## Périmètre
- `src/plugins/**`.
- Tests propres aux plugins.
- Manifestes, contrats consommés côté plugin, packaging et compatibilité plugins.
- Migrations, refactorings et performances des plugins.

## Frontière stricte
Le rôle `plugins` ne modifie pas le Core pour satisfaire un besoin plugin. Si une primitive/API Core manque, il crée ou enrichit une issue `core` et poursuit uniquement la partie plugin réalisable.

Le code plugin dépend uniquement de l'API publique Core.
