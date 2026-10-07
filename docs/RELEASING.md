# Publier une GitHub Release

Le workflow `.github/workflows/release.yml` est la source de vérité pour publier CIFERECA.

## Déclenchement

Deux modes sont supportés :

- pousser un tag `v<VERSION>` ;
- lancer manuellement le workflow **Release** avec `release_tag=v<VERSION>`.

Le contenu de `VERSION` doit être strictement identique à `project.version` dans `pyproject.toml`.

## Validation préalable

Avant toute publication, le workflow :

1. valide la cohérence du tag et des métadonnées ;
2. exécute `python -m pytest -q tests` sous Python 3.11 ;
3. valide le packaging ;
4. construit le wheel Core, le bundle plugins et le snapshot de développement ;
5. génère `SHA256SUMS`.

Une release n'est publiée qu'après succès de ces étapes.

## Prereleases

Les versions contenant un suffixe PEP 440 de prerelease (`aN`, `bN`, `rcN`) ou un local version identifier (`+...`) sont publiées comme prereleases GitHub.

## Publication sûre et relançable

La publication crée ou met à jour d'abord une release en brouillon, charge les assets avec `--clobber`, puis enlève le statut brouillon seulement à la fin. Une relance peut donc réutiliser le même tag et remplacer les assets sans produire une seconde release.

Les assets publiés sont ceux présents dans `dist/` : wheel Core, ZIP de distribution, snapshot de développement et `SHA256SUMS`.
