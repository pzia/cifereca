# Cifereca — instructions pour les agents

Ces règles s'appliquent à tout le dépôt.

## Frontières d'architecture

- Le Core vit sous `src/cifereca/`.
- Les plugins vivent sous `plugins/<plugin>/` et leurs tests spécifiques restent avec eux.
- Le code de production d'un plugin ne doit dépendre que de l'API publique `cifereca.plugin_api`, pas des modules privés du Core.
- `tests/` couvre le Core et les scénarios transverses.
- `gen-dataset/` est le banc de simulation fonctionnelle actuel : faux services, données, profils et orchestration. Ses propres tests valident le banc de simulation, pas le comportement métier de Cifereca.
- `environnements/` décrit les environnements techniques d'exécution/test (GTK, Xvfb, D-Bus, bootstrap, capture).
- Ne pas déplacer ou renommer ces zones sans que la tâche le demande explicitement.

## Tests

Classer un test selon ce qu'il affirme, pas selon les dépendances qu'il utilise.

- Assertion propre à un plugin : `plugins/<plugin>/tests/`.
- Assertion Core : `tests/`.
- Scénario traversant plusieurs plugins/Core/IHM : test fonctionnel ou E2E central.
- Validation d'un faux service ou de son orchestrateur : `gen-dataset/tests/`.
- Validation de GTK/Xvfb/bootstrap : self-test sous `environnements/`.

Exécuter d'abord les tests les plus ciblés, puis la suite pertinente. Un `pytest` lancé à la racine ne couvre pas automatiquement les tests des plugins.

## Validation

- Ne pas annoncer une validation GUI sans avoir réellement utilisé un environnement GTK adapté.
- Ne pas confondre un self-test de l'environnement avec un test fonctionnel Cifereca.
- Éviter les services externes réels dans les tests déterministes lorsqu'un simulateur versionné existe.
- Ne pas versionner les caches, environnements virtuels, captures, ZIP de build ou autres artefacts générés sauf demande explicite.

## Skills

Pour les tâches spécialisées, utiliser les skills repo-locaux sous `.agents/skills/` :

- `cifereca-plugin` : création/modification/validation d'un plugin ;
- `cifereca-tests` : placement et stratégie de tests ;
- `cifereca-simulation` : faux services et profils fonctionnels ;
- `cifereca-release` : version, packaging et validation de livraison.
