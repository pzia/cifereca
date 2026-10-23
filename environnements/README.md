# Environnements Cifereca

Ce répertoire contient des **recettes machine/runtime reproductibles** pour exécuter ou tester Cifereca dans des contextes techniques particuliers.

**Point d'entrée E2E UX pour les agents : [E2E-UX.md](E2E-UX.md).**
Il précise les commandes vérifiées dans les sources, la distinction
runtime/simulation/scénarios, le niveau de preuve et les modalités GitHub
Actions (runners E2E standard autorisés depuis le passage en public).

Il ne contient ni code applicatif, ni faux services métier de référence. Il doit rester distinct :

- du Core et des plugins ;
- des tests fonctionnels ;
- de la simulation fonctionnelle (`simulation/` aujourd'hui, `simulation/` à terme).

Une recette peut fournir du bootstrap rootless, des lanceurs, des tests de validation, Xvfb/D-Bus et des outils de capture. Les caches de paquets, environnements virtuels, captures et autres artefacts générés ne sont pas des sources et ne doivent pas être versionnés.


### GTK4 sur Debian 13 isolé : cache prêt à l'emploi

**Réflexe prioritaire des agents :** [cache de 261 paquets Debian 13 GTK4 publié comme asset GitHub Releases](openai-chat-debian/GITHUB_RELEASE_CACHE.md), téléchargement et extraction sécurisés par [`fetch_release_cache.py`](openai-chat-debian/fetch_release_cache.py), puis `bootstrap.py --offline`. Contrairement aux artefacts temporaires d'Actions, la Release a une **URL stable**. Si GitHub lui-même est inaccessible, transférer seulement cette archive vérifiée depuis une machine connectée. Réserver le workflow de relais APT aux renouvellements du cache, pas à chaque agent.

## Choix de la recette : commencer ici

Avant toute recette GTK, capture, démonstration ou E2E exploratoire, identifier
l'hôte réel au lieu de tenter d'installer GTK au hasard :

```bash
cat /etc/os-release
python3 -c 'import sys, platform; print(sys.version.split()[0], platform.machine())'
```

Puis utiliser **une seule** recette :

| Hôte | Recette à utiliser |
| --- | --- |
| Debian 13 `trixie`, Python 3.13, x86_64 | `environnements/openai-chat-debian/` |
| ChatGPT Work Ubuntu 24.04 `noble`, Python 3.12, x86_64 | `environnements/chatgpt-work/` |

Ne pas reconstruire visuellement l'application et ne pas remplacer GTK par une
maquette lorsque la demande porte sur une capture ou une vidéo de Cifereca :
la preuve UX doit provenir de **la vraie application Cifereca**, lancée avec le
simulateur versionné sous `simulation/`, sur un display virtuel Xvfb.

### Ordre canonique pour une démonstration UX réelle

1. bootstrapper la recette GTK correspondant à l'hôte ;
2. valider GTK/libadwaita avec le `check_import` fourni par la recette ;
3. régénérer le profil de simulation demandé, par exemple :
   `python3 simulation/datasetctl.py rebuild ux-small` ;
4. contrôler `status` et `summary` du profil ;
5. lancer la vraie commande Cifereca dans l'environnement XDG du profil ;
6. faire tourner Cifereca sous le `headless`/Xvfb de la recette GTK ;
7. pour une image, utiliser le mécanisme compatible avec le harnais choisi : `datasetctl.py snapshot` pour le harnais historique à `.venv`, ou `datasetctl exec` + `capture_app.py` pour `openai-chat-debian` ;
8. pour une vidéo, enregistrer **le même `$DISPLAY` Xvfb** avec FFmpeg
   `x11grab` pendant que Cifereca tourne ;
9. conserver logs, capture/vidéo et paramètres du profil comme preuves.

Le simulateur et le runtime GTK sont complémentaires : `simulation/` fournit
les données/services/configurations fonctionnelles ; `environnements/` fournit
GTK, Xvfb, D-Bus et les outils de capture.

### Hôte réseau-isolé : utiliser GitHub comme relais pour les paquets

Si le bootstrap Debian échoue uniquement parce que le conteneur ne peut pas
résoudre `deb.debian.org`/`security.debian.org`, **ne pas abandonner GTK et
ne pas fabriquer une fausse vidéo**. Le relais GitHub n'est qu'une solution
**conditionnelle** et n'est nécessaire que pour installer le runtime
**local** en l'absence de réseau. Depuis la levée du blocage budgétaire
annoncée le 11/10/2026, les E2E peuvent aussi s'exécuter directement sur
les **runners standard** via `.github/workflows/e2e-smoke.yml`, sans
transférer les .deb :

1. créer une branche technique temporaire ; le modèle `openai-chat-debian/github-cache-relay.yml.example` est fourni ;
2. y ajouter un workflow déclenché uniquement sur cette branche ;
3. sur un runner GitHub, lancer un conteneur `debian:trixie` et exécuter
   `environnements/openai-chat-debian/bootstrap.py --download-only --deb-cache <dir>` ;
4. publier ce cache avec `actions/upload-artifact` ;
5. récupérer l'artefact via les outils GitHub disponibles dans l'agent ;
6. extraire le cache localement et relancer
   `bootstrap.py --offline --deb-cache <dir>` ;
7. supprimer/fermer la branche de relais lorsqu'elle n'est plus utile.

Le cache est un artefact généré : il ne doit pas être commité. Le lock produit
par le bootstrap permet de vérifier versions et SHA-256. Si APT a reçu une
mise à jour de sécurité depuis le lock précédent, conserver le lock réellement
produit par le runner et signaler la différence plutôt que forcer une version
devenue indisponible.

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
| `simulation/` | faux services, données et profils fonctionnels actuels |
| futur `simulation/` | cible unique de la simulation fonctionnelle |
| `tests/` | assertions Core/transversales |
| `plugins/*/tests/` | assertions propres à un plugin |

Un self-test GTK réussi ne prouve pas qu'un scénario fonctionnel Cifereca est correct. Inversement, un test métier avec doubles ne constitue pas une recette graphique réelle.

Voir `docs/SIMULATION.md`.
