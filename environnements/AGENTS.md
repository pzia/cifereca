# Instructions agents — Environnements de test

Hérite de `/AGENTS.md`.

Pour les recettes UX, lire obligatoirement [E2E-UX.md](E2E-UX.md) et
[le rôle UX](../.agents/roles/ux.md) avant de créer un harnais ou une
nouvelle configuration machine. Depuis la décision du 11/10/2026, les E2E GTK sur runners GitHub standard sont autorisés via le workflow existant `E2E Smoke` ; rester frugal et sélectionner les scénarios utiles.


Les environnements servent de support à la CI/CD ou à l'analyse UX :
- reproductibilité, bootstrap partagé, GTK/Xvfb, D-Bus, runners et modifications de `environnements/**` affectant les gates : rôle `cicd` ;
- campagnes E2E exploratoires avec simulation, screenshots, maintenance du simulateur fonctionnel et production d'issues UX : rôle `ux`.

Le rôle `ux` peut utiliser ces environnements pour reproduire des parcours, maintenir à niveau le simulateur fonctionnel nécessaire, capturer des preuves visuelles et qualifier des défauts, mais il n'implémente pas les corrections produit. Si une recette exige de changer le **bootstrap/runtime partagé**, transmettre à `cicd` ; ne pas modifier en concurrence le même fichier dans deux PR. Les parcours et fixtures fonctionnels restent à `ux` dans `simulation/**`.

Ne pas y déplacer de logique applicative. Un self-test d'environnement prouve uniquement que l'environnement fonctionne.

Les scénarios E2E **d'acceptation avant fusion** et leur sélection dans la CI relèvent de `cicd`, avec assertions maintenues par Core/Plugins selon leur contrat. Les campagnes E2E exploratoires et recettes UX ciblées restent indépendantes (`ux`) et ne constituent pas un gate de livraison implicite. Voir [la politique de livraison](../docs/AGENT-DELIVERY-POLICY.md).
