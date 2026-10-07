# Profils de rôles agents

Ces profils complètent `/AGENTS.md`. Un agent choisit un rôle principal par fil et reste dans ce périmètre autant que possible.

- `core-core` : logique et infrastructure Core.
- `core-ui` : IHM et contrats UI.
- `plugins` : architecture et cohérence transversale des plugins.
- `plugin-cible` : un plugin fonctionnel précis.
- `backlog` : découverte, découpage et priorisation d'issues.
- `e2e` : validation de parcours réels de bout en bout.

Un changement transversal doit être découpé en tâches/handoffs par rôle plutôt qu'être absorbé par un seul fil.
