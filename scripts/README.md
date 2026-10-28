# Scripts de packaging plugins

## Recette jetable de main

`python3 scripts/recette.py` prépare puis lance une copie isolée et hors ligne
des données locales sur un SHA figé de main. Voir [le guide de recette](../docs/RECETTE-JETABLE.md)
pour les prérequis Linux, `--no-start`, `verify` et `destroy`.

Ces scripts construisent les artefacts à partir des sources versionnées sous
`plugins/<plugin>/`. Ils n'utilisent que la bibliothèque standard Python.

Le contrat snapshot est aligné sur **Cifereca Core 0.9.44-dev.1** :
`cifereca.dev_updater` attend une racine ZIP unique, un
`cifereca-dev.json` avec `format: 1` et
`kind: "cifereca-dev-snapshot"`, puis des packages plugins référencés par
`path`, `sha256` et `activate`.

## Vérification

```bash
python scripts/verify-packaging.py
python -m unittest scripts.test_plugin_packaging
```

## ZIP d'un plugin

```bash
python scripts/package-plugin.py plugins/baen
```

Produit par exemple :

```text
dist/cifereca-plugin-provider-baen-0.1.6.zip
```

Le `cifereca-plugin.json` est placé **à la racine** du ZIP, comme l'exige
l'installateur Core044.

## ZIP all-in-one

```bash
python scripts/package-all-in-one.py
```

Produit :

```text
dist/cifereca-plugins-all-in-one-<git-sha>.zip
  cifereca-plugins.json
  SHA256SUMS
  packages/
    cifereca-plugin-....zip
    ...
```

Ce bundle est un artefact de distribution : les ZIP individuels qu'il contient
sont les packages installables.

## Snapshot pour cifereca-update-dev

```bash
python scripts/package-dev-snapshot.py
```

Produit :

```text
dist/cifereca-dev-plugins-<git-sha>.zip
  cifereca-dev-plugins-<git-sha>/
    cifereca-dev.json
    packages/
      SHA256SUMS
      cifereca-plugin-....zip
      ...
```

Le snapshot est **plugin-only** ; Core044 accepte explicitement un snapshot qui
contient des plugins sans archive Core.

Pour demander une validation réelle par le binaire installé :

```bash
python scripts/package-dev-snapshot.py --validate-with-update-dev
```

Cela exécute :

```bash
cifereca-update-dev <snapshot.zip> --dry-run --json
```

Le script échoue si le snapshot est refusé.

## Règles appliquées

- package plugin : `cifereca-plugin.json` à la racine ;
- chemins absolus, `..` et symlinks refusés ;
- limites Core044 reprises : 5 000 fichiers / 256 MiB par plugin ;
- snapshot : racine unique, 30 000 fichiers / 2 GiB ;
- SHA-256 de chaque ZIP plugin inscrit dans `cifereca-dev.json` ;
- archives déterministes pour un même arbre de sources ;
- caches Python, venv et répertoires de build exclus.
