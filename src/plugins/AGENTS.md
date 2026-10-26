# Instructions agents — Plugins

Hérite de `/AGENTS.md`.

Rôle propriétaire unique : **`plugins`**.

Le rôle couvre un plugin unique comme les évolutions transversales de plusieurs plugins.

Le code de production d'un plugin ne dépend que de l'API publique `cifereca.plugin_api`.

Ne pas corriger un manque du Core par import privé, duplication locale ou modification Core : ouvrir/transmettre une issue au rôle `core`.

## Traces et diagnostics fournisseurs

Règle obligatoire : **[contrat de journalisation](../../docs/LOGGING-CONTRACT.md)** et **[skill](../../.agents/skills/cifereca-logging/SKILL.md)**. Les plugins exposent tous leurs événements et détails effectivement produits via les contrats publics ; pas d'expurgation locale par listes de champs et pas d'effacement de cause, URL, status HTTP ou message. **Seule l'IHM Core** pseudonymise **login/mot de passe**. Les exports bruts restent intacts. Aucun upload automatique de journaux ni capture supplémentaire induite.
