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
- Validation d'un faux service ou de son orchestrateur : `simulation/tests/`.
- Validation de GTK/Xvfb/bootstrap : self-test sous `environnements/`.

Ne jamais restaurer en bloc les anciens tests `_bundled`. Les invariants historiques doivent être migrés méthode par méthode sans chaîne de dépendances vers d'anciens tests.

Voir `docs/TEST-ORGANIZATION.md`.

Exécuter d'abord les tests les plus ciblés, puis la suite pertinente. Un `pytest` lancé à la racine ne couvre pas automatiquement les tests des plugins.

## Simulation et environnements

- `simulation/` est le banc de simulation fonctionnelle **actuel** : faux services, données, profils et orchestration.
- `simulation/` est le référentiel versionné de simulation fonctionnelle ; le travail restant concerne sa rationalisation et sa maintenance, pas sa création.
- Ne pas construire deux environnements de simulation concurrents : rationaliser progressivement les faux services, profils et outils dans `simulation/` et retirer les éléments devenus inutiles.
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
- `cicd` (Merger) : CI/CD, déblocage et merge des PR. À chaque itération, traiter d'abord les défauts/gates CI/CD, puis les PR à merger. Le Merger est aussi responsable du lancement des releases, **uniquement sur commande explicite** : la demande de release implique la création/vérification du tag **et** le déclenchement effectif du packaging et de la publication des artefacts. Un simple merge, un « next » ou une préparation de version n'autorise jamais une release.
- `core` : Core métier et Core UI. À chaque itération, traiter d'abord les sujets Core non-UI, puis les sujets Core UI.
- `plugins` : tous les plugins fonctionnels, leurs tests, leur packaging et leurs évolutions, qu'elles concernent un ou plusieurs plugins.
- `ux` : tickets UX, campagnes E2E exploratoires avec simulation/screenshots/vidéos, maintenance du simulateur fonctionnel et du harnais d'environnement GTK nécessaire à ces parcours ; transforme les constats en issues sans modifier le produit.

### Procédure obligatoire du rôle `ux` pour une preuve GTK

Quand une demande UX implique « lancer Cifereca », « faire une démo », « capturer
l'écran » ou « faire une vidéo », le rôle `ux` doit commencer par
`environnements/README.md` puis suivre la recette correspondant à l'hôte.

Ordre attendu :

1. identifier distribution, version Python et architecture ;
2. choisir `environnements/openai-chat-debian/` pour Debian 13/Python 3.13 ou
   `environnements/chatgpt-work/` pour Ubuntu 24.04/Python 3.12 ;
3. bootstrapper et valider GTK/libadwaita avec le `check_import` de la recette ;
4. si le réseau Debian local est bloqué, utiliser **GitHub Actions comme relais**
   pour produire le cache `.deb`, récupérer l'artefact via les outils GitHub de
   l'agent, puis bootstrapper offline ;
5. régénérer le profil demandé avec `simulation/datasetctl.py rebuild <profil>`,
   puis vérifier `status` et `summary` ;
6. lancer la **vraie application Cifereca** dans les XDG du profil sous le Xvfb
   du harnais GTK ;
7. pour une vidéo, enregistrer ce même `$DISPLAY` avec FFmpeg `x11grab` pendant
   le parcours et conserver les logs/captures comme preuves.

Une image ou vidéo reconstruite graphiquement, une maquette, un terminal filmé
ou le seul self-test GTK **ne constitue pas** une démonstration fonctionnelle
de Cifereca. Les faux services/données doivent venir de `simulation/`, pas
d'une réimplémentation ad hoc.

### Incréments et groupes atomiques

- Tous les rôles avancent par incréments priorisés : examiner les sujets actifs, choisir les plus importants, puis regrouper dans une même unité de travail uniquement les changements cohérents, testables et atomiques.
- Débloquer un merge fait partie du travail incrémental : diagnostiquer les conflits, gates, dépendances et incompatibilités ; le rôle propriétaire corrige dans son périmètre, tandis que `cicd` coordonne la validation et réalise seul le merge.
- Ne pas accumuler des sujets sans lien dans une PR ; préserver une validation et un rollback compréhensibles par groupe.
- Aucun rôle ne doit déclencher une release sans demande explicite ; seul `cicd` (Merger) l'exécute, tag et packaging compris.

### Règles communes de rôle

1. Le périmètre d'un rôle est strict : aucun code, test, workflow ou merge hors périmètre.
2. Lorsqu'une évolution hors périmètre est nécessaire, créer ou enrichir une issue pour le rôle propriétaire et poursuivre uniquement ce qui reste dans le périmètre courant.
3. `cicd` traite d'abord la chaîne CI/CD ; seulement ensuite il traite les merges prêts.
4. `core` traite d'abord le Core métier/services/API/stockage/framework ; seulement ensuite le Core UI.
5. `plugins` peut travailler sur un plugin unique ou plusieurs plugins, mais ne modifie pas le Core pour satisfaire un besoin plugin : il ouvre une issue `core`.
6. `ux` et `backlog` sont non implémentants côté produit. `ux` peut exécuter des E2E exploratoires, maintenir le simulateur fonctionnel et les recettes `environnements/` nécessaires à ces parcours, et produire screenshots/vidéos ; sa sortie produit reste une issue/backlog.
7. Avant tout chantier significatif, vérifier les issues, PR et branches actives afin d'éviter les collisions et doublons.
8. Une PR ne peut être mergée que par le rôle `cicd`.
9. En cas d'ambiguïté, choisir le rôle propriétaire du composant à modifier ; si cela implique de changer de rôle, créer une issue au lieu de franchir la frontière.
10. Tant que l'issue #310 reste ouverte pour quota GitHub Actions épuisé, `cicd` peut merger malgré des checks rouges **uniquement** lorsque les jobs concernés n'ont jamais démarré (`steps` vide/null, aucun log de test) et que le diff est compris, mergeable, sans review/thread bloquant ni échec fonctionnel réel connu. Cette exception expire automatiquement à la fermeture de #310.
11. Une PR **ne doit jamais être réalignée, rebasée ou mise à jour sur le HEAD de sa branche cible au seul motif qu'elle est `behind`**. Le rôle `cicd` tente le merge de la PR dans son état courant. Un réalignement n'est justifié que par un conflit Git empêchant le merge, une règle de protection l'imposant explicitement, ou une incompatibilité fonctionnelle/technique concrète avec la branche cible. L'ancienneté ou le nombre de commits de retard ne constituent pas, à eux seuls, une incompatibilité. Éviter les rebases/updates préventifs qui créent du churn, invalident des validations ou déclenchent inutilement la CI.

Les profils détaillés sous `.agents/roles/` sont des compléments de ce fichier. `AGENTS.md` reste la source canonique.
