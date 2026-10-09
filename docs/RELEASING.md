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


## Prévisualisations plugins-only (sans Core)

Pour un snapshot produit par `scripts/package_partial_plugins.py`, `cifereca-update-dev`
valide **avant toute sauvegarde ou mutation** l'index embarqué
`cifereca-plugins-only.json` : identifiant de snapshot, origine,
sélection exacte des packages, versions et SHA-256, contraintes Plugin API.

**Compatibilité volontairement stricte** : la version du Core installé
(`runtime/current`) doit être **exactement** `core_version_tested`.
Cette version testée n'est pas une promesse de compatibilité avec un Core
plus ancien ou plus récent. Chaque package doit accepter la Plugin API
installée. En cas d'échec, l'opération est refusée, y compris en
`--dry-run`, avant d'installer quoi que ce soit.

Un index plugins-only interdit explicitement une archive Core. Les nouveaux
snapshots partiels portent aussi `scope: "plugins-only"` dans le manifest
principal `cifereca-dev.json`. **Si l'index manque, l'updater refuse le ZIP
avant toute sauvegarde ou mutation**, même si les archives plugins sont
valides. Un marqueur de portée inconnu est également refusé.

Les anciens snapshots de développement **génériques** sans index et sans
marqueur continuent d'être acceptés selon leurs règles historiques. Le
format ne fournit **pas** de signature cryptographique : un adversaire capable
de réécrire à la fois le marqueur et l'index doit être contré par la
vérification de provenance du ZIP et de sa somme SHA-256 depuis une source fiable.
La publication officielle, les tags et cette authentification restent
à coordonner dans #787 et #577.
