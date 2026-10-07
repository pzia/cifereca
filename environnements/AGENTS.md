# Instructions agents — Environnements de test

Hérite de `/AGENTS.md`.

Les environnements servent de support à la CI/CD ou à l'analyse UX :
- reproductibilité, bootstrap, GTK/Xvfb, D-Bus, runners : rôle `cicd` ;
- campagnes E2E exploratoires avec simulation, screenshots et production d'issues UX : rôle `ux`.

Le rôle `ux` peut utiliser ces environnements pour reproduire des parcours, capturer des preuves visuelles et qualifier des défauts, mais il n'implémente pas les corrections.

Ne pas y déplacer de logique applicative. Un self-test d'environnement prouve uniquement que l'environnement fonctionne.
