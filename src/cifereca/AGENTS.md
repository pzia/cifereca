# Instructions agents — Core

Hérite de `/AGENTS.md`.

Pour toute tâche dans ce répertoire, choisir principalement :

- **`core-core`** pour modèle métier, orchestration, services, stockage, configuration, API publique et framework de plugins.
- **`core-ui`** pour GTK/libadwaita, navigation, widgets, mise en page et primitives UI.

Ne pas déplacer dans le Core une logique propre à un plugin. Si la modification demandée est spécifique à un plugin, faire un handoff vers `plugin-cible`.

Si un changement d'API publique est nécessaire pour plusieurs plugins, coordonner avec le rôle `plugins`.

Profils : `/.agents/roles/core-core.md` et `/.agents/roles/core-ui.md`.
