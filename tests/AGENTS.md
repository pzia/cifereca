# Instructions agents — Tests centraux

Hérite de `/AGENTS.md`.

- invariant Core, contrat transversal Core ou test Core UI : rôle `core` ;
- orchestration, découverte, stabilité ou chaînage des suites : rôle `cicd` ;
- assertion propre à un plugin : rôle `plugins` ;
- E2E exploratoire, simulation de parcours, screenshots et qualification UX : rôle `ux`.
- assertions fonctionnelles E2E d'un changement produit : `core` ou `plugins` selon le comportement ; sélection, lancement, gates CI et artefacts : `cicd`.
- `e2e/run.py`, sélection/découverte et orchestration technique E2E : **`cicd`**, même si `ux` a découvert le besoin. `ux` garde les seuls parcours exploratoires ; toute modification d'un fichier E2E partagé requiert la lecture des PR concurrentes et l'accord du propriétaire de la résolution.

Le rôle `ux` utilise les E2E pour créer des issues documentées ; il ne corrige pas le produit et ne transforme pas un scénario exploratoire en gate CI sans handoff vers `cicd`.

Aucun rôle ne corrige hors de son périmètre : créer une issue pour le propriétaire concerné.

Les recettes humaines restent différables sauf exception de sécurité explicitement justifiée. Pour fusionner/fermer, appliquer [la politique commune](../docs/AGENT-DELIVERY-POLICY.md) : tests réellement passés, preuves ajustées au risque, aucune attente IHM utilisateur par défaut.
