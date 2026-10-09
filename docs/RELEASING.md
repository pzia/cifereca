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

Après l'installation, le smoke vérifie également que **chaque package
sélectionné et actif** figure bien dans l'inventaire du Core cible. Une
installation qui se termine sans erreur CLI mais perd un package actif
déclenche la restauration. Les packages volontairement inactifs ne sont
ni réactivés, ni considérés manquants par cette vérification.

Un index plugins-only interdit explicitement une archive Core. Les nouveaux
snapshots partiels portent aussi `scope: "plugins-only"` dans le manifest
principal `cifereca-dev.json`. **Si l'index manque, l'updater refuse le ZIP
avant toute sauvegarde ou mutation**, même si les archives plugins sont
valides. Un marqueur de portée inconnu est également refusé.

Les anciens snapshots de développement **génériques** sans index et sans
marqueur continuent d'être acceptés selon leurs règles historiques. Le
format ne fournit **pas** de signature cryptographique : un adversaire capable
de réécrire à la fois le marqueur et l'index doit être contré par la
vérification de provenance du ZIP depuis une source fiable.

`cifereca-update-dev --expected-sha256 <64 chiffres hexadécimaux>` permet
un contrôle du SHA-256 **obtenu d'un canal indépendant et authentifié**.
La comparaison est effectuée avant extraction et avant toute mutation,
y compris en `--dry-run`. L'option demeure facultative pour les snapshots
historiques. Un `.zip.sha256` livré avec le ZIP sur un canal non fiable
ne constitue pas une preuve d'origine.

## Releases officielles plugins-only

Le workflow manuel `.github/workflows/plugins-only-release.yml` publie une release
partielle **sans Core** à partir de `main`. Le tag doit suivre
`plugins-v<CORE_VERSION>-<SERIAL>` et le ZIP embarque l'index
`cifereca-plugins-only.json` produit par le même packager que la preview.

Le workflow échoue avant publication si le runtime installé ne fournit pas le mode
strict `--require-plugins-only`, si le Core/API cible n'est pas compatible, si le
snapshot n'est pas strictement plugins-only ou si les tests updater/rollback ciblés
échouent.

Le ZIP publié reçoit une attestation SLSA signée par GitHub Actions via
`actions/attest@v4`. La confiance ne repose donc pas sur le sidecar `.sha256`
pris isolément : l'utilisateur vérifie d'abord l'attestation et l'identité du
workflow signataire, puis passe le SHA-256 des octets vérifiés à l'updater.

Exemple :

~~~bash
gh release download <TAG> -R pzia/cifereca
gh attestation verify <TAG>.zip \
  -R pzia/cifereca \
  --signer-workflow pzia/cifereca/.github/workflows/plugins-only-release.yml \
  --source-ref refs/heads/main
sha256sum -c <TAG>.zip.sha256
digest="$(sha256sum <TAG>.zip | awk '{print $1}')"
cifereca-update-dev <TAG>.zip \
  --require-plugins-only \
  --expected-sha256 "$digest" \
  --dry-run --json
cifereca-update-dev <TAG>.zip \
  --require-plugins-only \
  --expected-sha256 "$digest" \
  --json
~~~

Cette tranche P0 apporte la provenance vérifiable et le refus strict avant mutation.
Les évolutions plus larges de releases partielles (UI de provenance, compatibilité
Core multi-version, automatisation de sélection) restent dans #577.
