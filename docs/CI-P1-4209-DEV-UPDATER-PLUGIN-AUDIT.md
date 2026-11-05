# Audit CI P1 #4209 — dev-updater (05/11/2026)

## Périmètre et décision

Seul le package de consultation **dev-updater** s'ajoute à
`audited_product_packages`, après `literary-criticism`.
Les **19 autres packages** restent non audités : leur modification de code
déclenche les 21 suites, sans réduction implicite de couverture.
Aucun changement de workflow, produit ou sélecteur de tests.

## Preuves dans les sources et le manifest

- `dev_updater_page.py` : import de `cifereca.plugin_api.UiPageContribution`
  et de `github_releases` **du même package**. Page opt-in de lecture
  des releases, sans acquisition ni mutation.
- `github_releases.py` : import du service Core
  `cifereca.plugin_api.HTTPX_TRANSPORT`, de `httpx`, de `packaging`
  et de modules standard ; aucun import d'un autre package plugin.
- `cifereca-plugin.json` : une contribution
  `ui.page.dev-updater` de catégorie `ui.page` ; ni `requires`,
  ni `recommends`, ni `requires_contracts`. Les seuls brokers déclarés
  sont `tasks` et `httpx` (Core).
- Les 21 manifests courants ne déclarent pas de
  `requires_contracts` sur la catégorie générique `ui.page` ;
  le graphe du sélecteur vérifie néanmoins toute nouvelle dépendance
  par ID et par contrat. Aucun consommateur d'un autre package trouvé
  par recherche `dev-updater`, `dev_updater`,
  `dev_updater_page`, `github_releases` ou
  `ui.page.dev-updater` dans les sources/tests/manifests plugins.

## Contrats CI

`tests/test_ci_plugin_selection.py` impose :
- sélection **1/21** sur modification isolée
  `src/plugins/dev-updater/github_releases.py` ;
- fermeture transitive exactement `dev-updater` ;
- repli **21/21** si Baen non audité est modifié, seul ou avec
  dev-updater, ou si manifest/helper/test non autonome, suppression
  ou renommage ;
- échec du contrôle si un nouveau consommateur apparaît dans le
  code, les tests ou manifests d'un autre package.

Les tests Core de mise à jour et de packaging qui utilisent le
manifeste dev-updater restent soumis aux gates **Core/Packaging**
existants ; ce ciblage ne modifie que les **suites de packages plugins**
sur un changement produit isolé. Tout nouveau consommateur non couvert
impose de réexaminer le graphe avant d'élargir la sélection.

## Validation

Attendre PR CI sur le HEAD de cette PR : changement de
`.github/plugin-test-dependencies.json` ⇒ contrôle exhaustif Core et
plugins. Le gain de temps ciblé reste à mesurer sur une future
modification produit de dev-updater ; aucun chiffre de gain runtime
non observé n'est revendiqué.
