# Environnements Cifereca

Ce répertoire contient des **recettes machine/runtime reproductibles** pour exécuter ou tester Cifereca dans des contextes techniques particuliers.

Il ne contient ni code applicatif, ni faux services métier de référence. Il doit rester distinct :

- du Core et des plugins ;
- des tests fonctionnels ;
- de la simulation fonctionnelle (`gen-dataset/` aujourd'hui, `simulation/` à terme).

Une recette peut fournir du bootstrap rootless, des lanceurs, des tests de validation, Xvfb/D-Bus et des outils de capture. Les caches de paquets, environnements virtuels, captures et autres artefacts générés ne sont pas des sources et ne doivent pas être versionnés.

## Recettes présentes

### `openai-chat-debian/`

Environnement Debian 13 / OpenAI GTK de référence :

- GTK4/libadwaita rootless ;
- Python/GTK isolé ;
- Xvfb et D-Bus privés ;
- exécution headless ;
- capture et interaction X11 ;
- cache Debian portable/offline.

Cette recette répond à la question « la machine/runtime permet-elle d'exécuter et piloter la vraie IHM ? ».

### `chatgpt-work/`

Environnement de travail spécifique à ChatGPT Work / Ubuntu 24.04 :

- bootstrap GTK rootless ;
- HOME/XDG isolés ;
- lancement Cifereca ;
- headless et capture ;
- boucle de travail UX dans la VM.

Cette recette répond à la question « comment reproduire la machine de travail ChatGPT Work ? ».

## Environnement, simulation et tests

| Zone | Responsabilité |
| --- | --- |
| `environnements/` | préparer/valider la machine et le runtime |
| `gen-dataset/` | faux services, données et profils fonctionnels actuels |
| futur `simulation/` | cible unique de la simulation fonctionnelle |
| `tests/` | assertions Core/transversales |
| `plugins/*/tests/` | assertions propres à un plugin |

Un self-test GTK réussi ne prouve pas qu'un scénario fonctionnel Cifereca est correct. Inversement, un test métier avec doubles ne constitue pas une recette graphique réelle.

Voir `docs/SIMULATION.md`.
