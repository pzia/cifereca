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

Chaque fil/agent choisit **un seul rôle** et ne sort jamais de son périmètre. Un besoin hors périmètre doit être consigné dans une issue destinée au rôle propriétaire, sans implémentation opportuniste.

Rôles autorisés :

- `backlog` : triage, déduplication, priorisation et création/enrichissement d'issues ; aucune implémentation.
- `cicd` : CI/CD **et merge**. À chaque itération, traiter d'abord les défauts/gates CI/CD, puis les PR à merger.
- `core` : Core métier et Core UI. À chaque itération, traiter d'abord les sujets Core non-UI, puis les sujets Core UI.
- `plugins` : tous les plugins fonctionnels, leurs tests, leur packaging et leurs évolutions, qu'elles concernent un ou plusieurs plugins.
- `ux` : observation/validation UX, UI et parcours ; produit uniquement du backlog/des issues, sans modifier le produit.

### Règles communes de rôle

1. Le périmètre d'un rôle est strict : aucun code, test, workflow ou merge hors périmètre.
2. Lorsqu'une évolution hors périmètre est nécessaire, créer ou enrichir une issue pour le rôle propriétaire et poursuivre uniquement ce qui reste dans le périmètre courant.
3. `cicd` traite d'abord la chaîne CI/CD ; seulement ensuite il traite les merges prêts.
4. `core` traite d'abord le Core métier/services/API/stockage/framework ; seulement ensuite le Core UI.
5. `plugins` peut travailler sur un plugin unique ou plusieurs plugins, mais ne modifie pas le Core pour satisfaire un besoin plugin : il ouvre une issue `core`.
6. `ux` et `backlog` sont non implémentants. Ils peuvent inspecter le dépôt et les exécutions, mais leur sortie est une issue/backlog.
7. Avant tout chantier significatif, vérifier les issues, PR et branches actives afin d'éviter les collisions et doublons.
8. Une PR ne peut être mergée que par le rôle `cicd`.
9. En cas d'ambiguïté, choisir le rôle propriétaire du composant à modifier ; si cela implique de changer de rôle, créer une issue au lieu de franchir la frontière.

Les profils détaillés sous `.agents/roles/` sont des compléments de ce fichier. `AGENTS.md` reste la source canonique.
