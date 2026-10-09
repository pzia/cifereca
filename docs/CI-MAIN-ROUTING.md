# Routage des validations lourdes sur main — P0 #856

Les workflows `Test Core`, `Test plugins` et `Validate installed Core` gardent
`workflow_dispatch` pour une recette complète manuelle. Leur trigger
`push` sur `main` utilise désormais les chemins dépendants (`paths`).
Le gate PR `PR CI` est séparé et inchangé.

| Changement de fichiers | Core | Plugins | Wheel installée |
| --- | :---: | :---: | :---: |
| `docs/**`, `AGENTS.md`, `.agents/**` seuls | — | — | — |
| `src/plugins/**` seul | — | ✓ | — |
| `src/cifereca/gui/**`, `builtin_ui/**`, `app.py` seuls | ✓ | — | ✓ |
| Core partagé, API et gestionnaire de plugins | ✓ | ✓ | ✓ |
| `pyproject.toml`, `VERSION` | ✓ | ✓ | ✓ |
| `tests/**` / `e2e/**` | ✓ | — | — |
| `scripts/ci-run-plugin-tests.sh` | — | ✓ | — |
| `scripts/ci-smoke-installed-wheel.sh` | — | — | ✓ |
| Commit mixte | Union des suites | | |

Les tests Core couvrent également le sélecteur PR, le contrat E2E,
les scripts de packaging importés par `tests/**` et la préparation GTK.
`README.md` déclenche uniquement la validation wheel car ses métadonnées
de description entrent dans le paquet distribué.
La suite Plugins observe les changements Core partagés mais exclut
explicitement `gui/**`, `builtin_ui/**` et `app.py`.
Modifier le YAML de l'un de ces workflows déclenche sa propre suite.

## Maintien des invariants

- Conserver les motifs négatifs GitHub `!` **après** leur inclusion positive.
- Lorsqu'une nouvelle dépendance entre dans un des trois jobs, mettre à jour
  son filtre et `tests/test_ci_main_path_filters.py` dans la même PR.
- Un commit mixte active l'union des validateurs concernés. Pour un diff
  ambigu, un renommage ou une évolution transversale non classifiée,
  lancer manuellement les **trois** workflows avec `workflow_dispatch`.
  Les filtres GitHub ne remplacent pas la recette exhaustive pré-release.
- Ne pas utiliser `paths` pour des checks obligatoires de PR sans job
  sentinelle stable : un check filtré peut rester `pending`. Ces trois jobs
  sont des validations de push ; `PR CI` et les releases restent inchangés.
- Ne pas déclencher de release pour prouver une règle de sélection.
- Relever les vrais runs avant/après sur des commits docs, plugin, Core
  et mixtes avant de chiffrer les économies ; aucun pourcentage supposé.
- Les échecs GitHub Actions **sans étape/log** (incident P0 #888)
  restent distincts : le filtrage n'établit pas leur résolution.
  Réduire les jobs n'autorise pas la fusion sans les gates de PR.

## Recette reproductible

`python -m pytest -q tests/test_ci_main_path_filters.py` vérifie les cas
documentations/handoffs, plugins seuls, Core GUI ou API partagée,
scripts consommés, modifications mixtes, dispatch et exclusions.
Les workflows conservent également une possibilité de lancement manuel
pour valider le SHA de `main` lors d'un incident ou avant une release.

## Sélection prudente des packages de tests (P0 #856)

PR et push Plugins partagent `scripts/ci_select_plugin_tests.py`.
Seuls des ajouts/modifications de modules `src/plugins/<package>/tests/test_*.py`
(éventuellement accompagnés de docs) sélectionnent les packages concernés.
Code produit, manifeste, helper/conftest, suppression ou renommage imposent
la suite complète : les dépendances entre plugins ne sont pas encore modélisées.
Cette tranche ne prétend donc pas cibler un changement produit isolé.

Push : diff `before..sha`, historique complet et vérification du checkout.
PR : diff merge-base vers head, compatible avec le checkout de merge GitHub.
SHA absent/zéro, diff inaccessible, historique divergent ou dispatch manuel :
repli complet explicite, avec raison JSON dans les logs. Aucun skip silencieux.
Le runner existant reçoit les répertoires comme arguments Bash séparés.
