# Cifereca — instructions pour les agents

Ces règles s'appliquent à tout le dépôt.

## Frontières d'architecture

### État actuel

- Le Core applicatif vit sous `src/cifereca/`.
- Les plugins fonctionnels vivent sous `src/plugins/<plugin>/` et leurs tests spécifiques restent avec eux.
- `src/cifereca/plugins/` est le framework de plugins (loader, manager, manifeste, dépendances, état), pas l'emplacement des plugins fonctionnels.
- `src/cifereca/builtin_ui/` et l'orchestration résiduelle `src/cifereca/kobo/` restent à rationaliser. Le client Kobo-Vault appartient à `src/plugins/provider-kobo/`, et Paris à `src/plugins/provider-paris/`.
- `bundled_plugins/` et `integrations/` ne sont plus des sources actives.
- Le code de production d'un plugin ne doit dépendre que de l'API publique `cifereca.plugin_api`, pas des modules privés du Core.

Voir `docs/ARCHITECTURE.md` et `docs/PLUGIN-BOUNDARIES.md`.

### Cible

La cible structurelle distingue explicitement :

- `src/cifereca/` : Core, API publique, framework de plugins, GUI et primitives transversales ;
- `src/plugins/` : source canonique des plugins fonctionnels.

## Tests

Classer un test selon ce qu'il affirme, pas selon les dépendances qu'il utilise.

- Assertion propre à un plugin : `src/plugins/<plugin>/tests/`.
- Assertion Core, contrat transversal ou orchestration générique : `tests/`.
- Scénario traversant plusieurs plugins/Core/IHM : test fonctionnel ou E2E central.
- Validation d'un faux service ou de son orchestrateur : `simulation/tests/` tant que la migration vers `simulation/` n'est pas réalisée.
- Validation de GTK/Xvfb/bootstrap : self-test sous `environnements/`.

Ne jamais restaurer en bloc les anciens tests `_bundled`. Les invariants historiques doivent être migrés méthode par méthode sans chaîne de dépendances vers d'anciens tests.

Voir `docs/TEST-ORGANIZATION.md`.

Exécuter d'abord les tests les plus ciblés, puis la suite pertinente. Un `pytest` lancé à la racine ne couvre pas automatiquement les tests des plugins.

## Simulation et environnements

- `simulation/` est le banc de simulation fonctionnelle **actuel** : faux services, données, profils et orchestration.
- `simulation/` n'existe pas encore ; il est la **cible future** pour l'environnement fonctionnel simulé de référence.
- Ne pas construire deux environnements de simulation concurrents : les outils utiles de `simulation/` devront être migrés ou retirés progressivement.
- `environnements/` contient des recettes machine/runtime (GTK, Xvfb, D-Bus, bootstrap, capture), pas du code applicatif ni un substitut à la simulation fonctionnelle.

Voir `docs/SIMULATION.md` et `environnements/README.md`.

## Validation

- Ne pas annoncer une validation GUI sans avoir réellement utilisé un environnement GTK adapté.
- Ne pas confondre un self-test de l'environnement avec un test fonctionnel Cifereca.
- Éviter les services externes réels dans les tests déterministes lorsqu'un simulateur versionné existe.
- Ne pas versionner les caches, environnements virtuels, captures, ZIP de build ou autres artefacts générés sauf demande explicite.

## Rationalisation documentaire

`docs/RATIONALIZATION-STATUS.md` est l'index de référence pour distinguer :

- rationalisation terminée ;
- rationalisation en cours ;
- dette structurelle restante ;
- handoffs encore utiles ;
- handoffs devenus historiques.

Les anciens handoffs restent des traces de contexte ; en cas de contradiction, l'index de rationalisation et l'état réel du tree courant priment.

## Skills

Pour les tâches spécialisées, utiliser les skills repo-locaux sous `.agents/skills/` :

- `cifereca-plugin` : création/modification/validation d'un plugin ;
- `cifereca-tests` : placement et stratégie de tests ;
- `cifereca-simulation` : faux services et profils fonctionnels ;
- `cifereca-release` : version, packaging et validation de livraison.

## Rôles de travail des agents

Chaque fil/agent doit choisir **un rôle principal** avant de modifier le dépôt. Le rôle détermine son périmètre, ses fichiers privilégiés et les sujets qu'il doit transmettre à un autre fil plutôt que traiter opportunément.

Rôles disponibles :

- `core-core` : modèle métier, orchestration, services Core, API publique, stockage, configuration et framework de plugins ; voir `.agents/roles/core-core.md`.
- `core-ui` : IHM GTK/libadwaita, navigation, composants UI partagés, contrats UI publics et ergonomie Core ; voir `.agents/roles/core-ui.md`.
- `plugins` : architecture transversale des plugins, contrats, packaging, compatibilité et évolutions touchant plusieurs plugins ; voir `.agents/roles/plugins.md`.
- `plugin-cible` : travail sur un seul plugin fonctionnel, ses tests et sa documentation ; voir `.agents/roles/plugin-cible.md`.
- `backlog` : analyse du dépôt et création/priorisation d'issues, sans implémentation fonctionnelle par défaut ; voir `.agents/roles/backlog.md`.
- `e2e` : parcours utilisateur de bout en bout, simulation, environnement GTK, fixtures, captures et diagnostic CI ; voir `.agents/roles/e2e.md`.

### Règles communes de rôle

1. Annoncer ou consigner le rôle choisi dans le handoff, la branche ou l'issue de travail.
2. Ne pas modifier hors périmètre sauf changement minimal nécessaire à la tâche.
3. Lorsqu'un besoin appartient clairement à un autre rôle, créer ou enrichir une issue puis faire un handoff explicite.
4. Ne pas dupliquer un travail déjà pris par un autre fil : vérifier issues/PR/branches actives avant un chantier significatif.
5. Préférer des branches et PR petites, spécialisées par rôle.
6. Les changements de contrat public entre Core et plugins exigent coordination entre `core-core` et `plugins`.
7. Les changements visuels ou de parcours imposent une validation adaptée par `core-ui` ou `e2e`.
8. Le rôle `backlog` ne fusionne pas implémentation et triage dans la même tâche sauf demande explicite.
9. Le rôle `e2e` ne corrige pas silencieusement le produit pour faire passer un test : il ouvre/transmet le défaut au rôle propriétaire.
10. En cas d'ambiguïté, choisir le rôle correspondant au **composant propriétaire de la modification**, pas au symptôme observé.

Les profils détaillés sous `.agents/roles/` sont des compléments de ce fichier. `AGENTS.md` reste la source canonique des règles communes.

