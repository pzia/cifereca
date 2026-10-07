# Instructions agents — Plugins

Hérite de `/AGENTS.md`.

- Pour une modification limitée à un plugin : rôle **`plugin-cible:<nom>`**.
- Pour une modification transverse, une migration ou une convention touchant plusieurs plugins : rôle **`plugins`**.

Le code de production d'un plugin ne dépend que de l'API publique `cifereca.plugin_api`.

Ne pas corriger un manque du Core par import privé ou duplication locale : ouvrir/transmettre le besoin à `core-core`.
Une primitive UI générique manquante relève de `core-ui`.

Profils : `/.agents/roles/plugin-cible.md` et `/.agents/roles/plugins.md`.
