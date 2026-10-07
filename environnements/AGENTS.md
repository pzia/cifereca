# Instructions agents — Environnements de test

Hérite de `/AGENTS.md`.

Rôle principal : **`e2e`**.

Ce répertoire contient les recettes machine/runtime nécessaires à une exécution représentative : GTK, Xvfb, D-Bus, bootstrap, captures et diagnostics.

Ne pas y déplacer de logique applicative ni créer un second banc de simulation fonctionnelle.

Un self-test d'environnement prouve uniquement que l'environnement fonctionne ; il ne prouve pas qu'un parcours CIFERECA est valide.

Profil : `/.agents/roles/e2e.md`.
