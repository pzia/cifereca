# Instructions agents — Environnements de test

Hérite de `/AGENTS.md`.

Les environnements servent de support à la CI/CD ou à l'analyse UX :
- reproductibilité, bootstrap, GTK/Xvfb, D-Bus, runners : rôle `cicd` ;
- observation de parcours et production d'issues UX : rôle `ux`.

Ne pas y déplacer de logique applicative. Un self-test d'environnement prouve uniquement que l'environnement fonctionne.
