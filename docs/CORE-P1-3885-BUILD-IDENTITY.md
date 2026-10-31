# #3885 — Identité de build Core (P1)

Le journal de démarrage GUI émet une fois `DIAG_JSON` / `CORE` /
`build_identity`, **avant toute connexion fournisseur**. Le champ
`detail` contient `schema_version=1`, `status` (`known` ou
`unavailable`), `commit_sha` complet (ou `null`), `build_version`,
`build_timestamp`, `distribution_mode` et `origin`.

- Le wheel Core embarque `cifereca/_build_identity.json`. Le producteur
  `setup.py build_py` récupère le SHA depuis un **checkout Git propre
  correspondant au code empaqueté** ; un source tree modifié ou une archive
  sans `.git` produisent explicitement `commit_sha=null`.
- Pour `cifereca-update --github`, le reçu installé
  `.cifereca-install.json` contient le commit **résolu et sélectionné pour
  l'installation**. Ce reçu a autorité, sauf contradiction avec le wheel
  (qui conduit à `unavailable`). Les distributions archive/source sans preuve
  ne prétendent pas connaître le SHA.
- Aucun accès réseau, résolution de branche Git courante, chemin personnel
  ou secret n'est nécessaire au startup. L'ancienne fonction
  `startup_git_commit()` reste un *indice legacy* pour des clients existants ;
  sa variable `CIFERECA_GIT_COMMIT` ne peut **pas** modifier le diagnostic
  canonique ni contredire les octets du package.
- `cifereca-cli doctor --json` inclut `core.build_identity` et la même
  valeur canonique dans `core.commit`. L'édition texte affiche statut,
  origine, mode, timestamp et commit.
- Le futur manifeste `sync run --report` (#3882) doit réutiliser
  **exactement** `cifereca.startup_revision.build_identity()`, sans nouvelle
  recherche Git ni extraction depuis `main`.

## Validation

Les workflows PR CI, Validate installed Core et Release passent à
`ci-smoke-installed-wheel.sh` le commit exact du checkout GitHub. Le smoke
installe **le wheel produit** dans un venv et vérifie, hors source,
`_build_identity.json` + `build_identity()` égaux à
`GITHUB_SHA`. Sans SHA attendu (archive/sources locales), le smoke impose
la présence du manifeste et la cohérence de version mais autorise
`unavailable`. Tests unitaires de refus sur SHA malformé, version incompatible,
conflit entre reçu géré et wheel et variable d'environnement trompeuse.

La preuve de production nécessite un redéploiement puis la comparaison
du SHA journalisé avec le SHA explicitement sélectionné au déploiement.
