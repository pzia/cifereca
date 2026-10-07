# Instructions agents — Tests centraux

Hérite de `/AGENTS.md`.

- invariant Core, contrat transversal Core ou test Core UI : rôle `core` ;
- orchestration, découverte, stabilité ou chaînage des suites : rôle `cicd` ;
- assertion propre à un plugin : rôle `plugins` ;
- observation de parcours/ergonomie visant à produire des défauts : rôle `ux`.

Aucun rôle ne corrige hors de son périmètre : créer une issue pour le propriétaire concerné.
