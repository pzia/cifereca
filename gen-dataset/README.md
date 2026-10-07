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

Les fichiers runtime sont stockés dans `gen-dataset/.runtime/` par défaut.
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
python3 gen-dataset/datasetctl.py profiles

python3 gen-dataset/datasetctl.py up ux-search
python3 gen-dataset/datasetctl.py status ux-search
python3 gen-dataset/datasetctl.py summary ux-search
python3 gen-dataset/datasetctl.py logs ux-search
python3 gen-dataset/datasetctl.py config ux-search

python3 gen-dataset/datasetctl.py cifereca ux-search

python3 gen-dataset/datasetctl.py down ux-search
python3 gen-dataset/datasetctl.py rebuild ux-search
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

Les profils sont dans `gen-dataset/profiles/*.toml`.

## Lancer une commande dans l'environnement isolé

```bash
python3 gen-dataset/datasetctl.py exec ux-library -- \
  cifereca-cli catalog-scan
```

Pour récupérer les variables XDG :

```bash
python3 gen-dataset/datasetctl.py env ux-library
```

## Snapshot GTK

Le harnais GTK reste externe au dépôt. Son chemin peut être fourni par
`--harness` ou `CIFERECA_GTK_TEST_HARNESS`.

```bash
export CIFERECA_GTK_TEST_HARNESS=/chemin/gtk4-environnement-tests

python3 gen-dataset/datasetctl.py snapshot ux-library \
  --out snapshots/library.png \
  --width 1300 --height 850 --wait 4
```

Interactions X11 optionnelles :

```bash
python3 gen-dataset/datasetctl.py snapshot ux-search \
  --out snapshots/search.png \
  --click 408,185 \
  --type-text tour \
  --key Return
```

La capture appelle le couple `headless` / `capture-app` du harnais existant,
tout en conservant les faux services et la configuration Cifereca du profil.

## Ajouter un service

1. ajouter son script dans `gen-dataset/<service>/` ;
2. le déclarer dans `gen-dataset/services/catalog.toml` ;
3. ajouter les paramètres voulus dans un profil ;
4. si nécessaire, compléter la génération de configuration provider dans
   `datasetctl.py`.

Les données de livre, couvertures PNG et EPUB doivent réutiliser
`gen-dataset/common/fake_books.py` quand leur nature est générique. Les
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
python3 gen-dataset/datasetctl.py doctor ux-mon-test
```
