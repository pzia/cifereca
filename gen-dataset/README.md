# Datasets UX — mode d'emploi

`datasetctl.py` orchestre les faux services Cifereca dans un environnement UX
entièrement isolé.

## Principes

- profils TOML versionnés ;
- ports fixes et health checks ;
- caches persistants par profil ;
- génération automatique de `config.json` et `plugins-config.json` ;
- isolation via `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME` et
  `XDG_STATE_HOME` ;
- génération optionnelle d'une bibliothèque EPUB locale ;
- lancement Cifereca avec `--no-plugins` et les vrais `--force-plugin` du Core ;
- exécution de n'importe quel outil UX dans le même environnement ;
- capture directe via le harnais GTK/Xvfb `gtk4-environnement-tests`.

Les fichiers runtime sont stockés dans `gen-dataset/.runtime/` par défaut.
Définissez `CIFERECA_DATASET_RUNTIME` pour les placer ailleurs.

## Commandes

```bash
python3 gen-dataset/datasetctl.py profiles

python3 gen-dataset/datasetctl.py up ux-search
python3 gen-dataset/datasetctl.py status ux-search
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

## Profils livrés

| Profil | Usage |
| --- | --- |
| `ux-small` | recette rapide de tous les services |
| `ux-search` | recherche multi-source à gros volume |
| `ux-library` | bibliothèque dense : EPUB locaux + Kobo + Numilog |
| `ux-paris` | parcours Paris / PNB |
| `ux-errors` | états atypiques et erreurs visibles |
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

Les données de livre, couvertures et EPUB doivent réutiliser
`gen-dataset/common/fake_books.py` quand leur nature est générique. Les
protocoles et états métier restent propres aux services.

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
