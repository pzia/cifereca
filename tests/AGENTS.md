# Instructions agents — Tests centraux

Hérite de `/AGENTS.md`.

- invariant Core, contrat transversal Core ou test Core UI : rôle `core` ;
- orchestration, découverte, stabilité ou chaînage des suites : rôle `cicd` ;
- assertion propre à un plugin : rôle `plugins` ;
- E2E exploratoire, simulation de parcours, screenshots et qualification UX : rôle `ux`.

Le rôle `ux` utilise les E2E pour créer des issues documentées ; il ne corrige pas le produit et ne transforme pas un scénario exploratoire en gate CI sans handoff vers `cicd`.

Aucun rôle ne corrige hors de son périmètre : créer une issue pour le propriétaire concerné.
