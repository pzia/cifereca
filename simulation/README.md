# Datasets UX — mode d'emploi

> **Position dans la rationalisation**  
> Ce répertoire est la simulation fonctionnelle **actuelle**. La cible future est `simulation/`, actuellement absente. Les faux services, fixtures et outils utiles seront migrés progressivement ; les éléments devenus inutiles seront supprimés. Ne pas développer en parallèle un second banc de simulation concurrent. Voir `../docs/SIMULATION.md`.

`datasetctl.py` orchestre les faux services Cifereca dans un environnement UX
entièrement isolé.

## Principes

- profils TOML versionnés ;
- ports fixes et health checks ;
- caches persistants par profil ;
- génération automatique de `config.json` et `plugins-config.json` ;
- isolation via `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME` et
  `XDG_STATE_HOME` ;
- snapshot des packages de plugins installés dans le profil UX, afin de garder
  BnF/Numilog/Home/etc. disponibles sans partager la base Cifereca réelle ;
- génération optionnelle d'une bibliothèque EPUB locale ;
- lancement Cifereca avec `--no-plugins` et les vrais `--force-plugin` du Core ;
- exécution de n'importe quel outil UX dans le même environnement ;
- capture directe via le harnais GTK/Xvfb `gtk4-environnement-tests`.

Les fichiers runtime sont stockés dans `simulation/.runtime/` par défaut.
Définissez `CIFERECA_DATASET_RUNTIME` pour les placer ailleurs.

Par défaut, `datasetctl` copie le store de plugins actif depuis le
`XDG_DATA_HOME` réel vers le profil isolé. Pour utiliser un store explicite :

```bash
export CIFERECA_DATASET_PLUGIN_SOURCE=~/.local/share/cifereca/plugins/packages
```

Cette copie n'est refaite que lorsque la signature des packages installés
change.

## Commandes

```bash
python3 simulation/datasetctl.py profiles

python3 simulation/datasetctl.py up ux-search
python3 simulation/datasetctl.py status ux-search
python3 simulation/datasetctl.py summary ux-search
python3 simulation/datasetctl.py logs ux-search
python3 simulation/datasetctl.py config ux-search

python3 simulation/datasetctl.py cifereca ux-search

python3 simulation/datasetctl.py down ux-search
python3 simulation/datasetctl.py rebuild ux-search
```

`up` est idempotent : un service déjà sain avec la même configuration est
réutilisé.

`rebuild` arrête le profil, supprime son état Cifereca isolé, ses caches de
services et sa bibliothèque générée, puis reconstruit le tout.

`summary` compare les volumes configurés aux volumes réellement générés et
aux compteurs annoncés par les health checks. `summary --json` fournit une
sortie exploitable par les scripts de recette.

## Profils livrés

| Profil | Usage |
| --- | --- |
| `ux-small` | recette rapide de tous les services |
| `ux-search` | recherche multi-source à gros volume |
| `ux-library` | bibliothèque dense : EPUB locaux + Kobo + Numilog |
| `ux-paris` | parcours Paris / PNB |
| `ux-errors` | états atypiques, assets OPDS/Kobo cassés de façon déterministe et erreurs visibles |
| `ux-all` | environnement complet |

Les profils sont dans `simulation/profiles/*.toml`.

## Lancer une commande dans l'environnement isolé

```bash
python3 simulation/datasetctl.py exec ux-library -- \
  cifereca-cli catalog-scan
```

Pour récupérer les variables XDG :

```bash
python3 simulation/datasetctl.py env ux-library
```


## Fixtures E2E reproductibles

Les scénarios E2E utilisent des déclarations versionnées dans
`simulation/e2e-fixtures/*.toml`. Chaque fixture référence un profil de
simulation dédié, déclare explicitement son état initial et utilise un nom de
runtime `e2e-*` séparé des profils UX normaux.

Commandes :

```bash
python3 simulation/e2e_fixtures.py list
python3 simulation/e2e_fixtures.py describe smoke
python3 simulation/e2e_fixtures.py prepare smoke
python3 simulation/e2e_fixtures.py reset smoke

python3 simulation/e2e_fixtures.py run empty -- \
  python3 -c 'print("scenario")'
```

`prepare` commence toujours par un reset complet de l'état isolé. `run`
garantit le nettoyage dans un `finally`, même si la commande échoue. Les
fixtures désactivent la copie du store de plugins utilisateur afin qu'un test
ne dépende jamais d'un profil réel. Le manifeste généré
`e2e-fixture.json` expose le profil, les services attendus, les chemins XDG
et l'empreinte déterministe de la bibliothèque locale.

Les fixtures livrées sont :

- `empty` : aucune donnée locale et aucun faux service ;
- `smoke` : 3 EPUB locaux, OPDS simulé et Paris simulé, avec graines fixes.

## Snapshot GTK

Le harnais GTK reste externe au dépôt. Son chemin peut être fourni par
`--harness` ou `CIFERECA_GTK_TEST_HARNESS`.

```bash
export CIFERECA_GTK_TEST_HARNESS=/chemin/gtk4-environnement-tests

python3 simulation/datasetctl.py snapshot ux-library \
  --out snapshots/library.png \
  --width 1300 --height 850 --wait 4
```

Interactions X11 optionnelles :

```bash
python3 simulation/datasetctl.py snapshot ux-search \
  --out snapshots/search.png \
  --click 408,185 \
  --type-text tour \
  --key Return
```

La capture appelle le couple `headless` / `capture-app` du harnais existant,
tout en conservant les faux services et la configuration Cifereca du profil.

## Ajouter un service

1. ajouter son script dans `simulation/<service>/` ;
2. le déclarer dans `simulation/services/catalog.toml` ;
3. ajouter les paramètres voulus dans un profil ;
4. si nécessaire, compléter la génération de configuration provider dans
   `datasetctl.py`.

Les données de livre, couvertures PNG et EPUB doivent réutiliser
`simulation/common/fake_books.py` quand leur nature est générique. Les
protocoles et états métier restent propres aux services.

OPDS et Kobo acceptent `--broken-assets-every N` pour faire échouer de façon
reproductible la couverture et le téléchargement de chaque N-ième livre. Le
profil `ux-errors` active ce mécanisme.

## Ajouter un profil

Un profil minimal :

```toml
name = "ux-mon-test"
description = "Mon scénario UX"

[cifereca]
binary = "cifereca"
no_plugins = true
force_plugins = ["ui.page.home"]
width = 1300
height = 850

[library]
epubs = 100
seed = 20261006

[services.bnf]
enabled = true
books = 20000
seed = 20261006
```

Validez-le avec :

```bash
python3 simulation/datasetctl.py doctor ux-mon-test
```

## JDD spécialisés — intégration dans datasetctl

Les profils `ux-small`, `ux-all` et `ux-errors` génèrent des fixtures
synthétiques **hors ligne** pour Jeunesse, Critique littéraire, Constellations
et Optimiseur EPUB. Elles sont déclarées par :

```toml
[specialized]
enabled = true
seed = 20261008
```

```sh
python3 simulation/datasetctl.py rebuild ux-small
python3 simulation/datasetctl.py env ux-small
python3 simulation/datasetctl.py summary ux-small --json
# données : ${CIFERECA_UX_SPECIALIZED_DATA}/youth.json et autres fichiers
```

Chaque profil possède son propre `runtime/specialized/`. Un manifeste avec
empreintes SHA-256 permet à `prepare` de conserver les fichiers inchangés,
réparer un fichier corrompu et reconstruire lors d'un changement de seed.
`rebuild` et `reset` suppriment ces fichiers avec le profil isolé.

**Attention :** leur disponibilité sur disque ne les injecte pas
automatiquement dans les caches des plugins, ne crée pas de source distante
et ne valide pas l'UI GTK. Les étapes HTTP/E2E restent suivies dans #772 et #774.
