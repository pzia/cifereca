# Audit CI P1 #4209 — constellations (05/11/2026)

## Portée

La tranche ajoute **un** package à la liste auditée :
`constellations`, après `dev-updater`,
`literary-criticism` et `maintenance-library-audit`.
Les **17 autres packages** restent non audités, avec un repli à
**21 suites de tests plugins** sur changement produit.

## Preuves et consommation

- Quatre modules de production : `constellations.py` (graphe
  et modèles), `constellation_view_model.py` (import interne
  `constellations` et API Core), `constellations_page.py`
  (import interne view model et API Core), `semantic_llm.py`
  (service `cifereca.plugin_api.LLMRequest`).
- Manifest : `navigation.constellations` de type
  `navigation.constellation`, et `ui.page.constellations`
  de type `ui.page` ; **aucun `requires`, `recommends`
  ni `requires_contracts`**. La page utilise les brokers
  Core `metadata`, `catalog`, `identities`, `tasks`.
- Aucun autre manifest plugin ne requiert les IDs ou
  la catégorie `navigation.constellation` ;
  aucun autre module de production plugin n'importe les modules
  spécifiques de ce package. Une mention des « Constellations »
  dans un commentaire du plugin Recommendations est une comparaison
  d'affichage, pas une dépendance de code.
- Les scénarios GTK de `E2E Smoke` déjà ciblés sur
  `src/plugins/constellations/**` restent **inchangés**.
  L'optimisation ne touche que la sélection des suites de
  packages plugins. Les tests fonctionnels du package sont conservés.

## Invariants contrôlés

`tests/test_ci_plugin_selection.py` vérifie la fermeture transitive
`constellations` seule sur modification de chacun des quatre modules,
et le fallback exhaustif sur changement non audité Baen, changement mixte,
manifest, helper partagé, suppression et renommage. Les références
à un module ou ID spécifique depuis les sources/manifests/tests d'un autre
plugin invalideront immédiatement l'audit.

PR CI exhaustive obligatoire sur cette PR puisque son graphe change.
Aucun temps de run ciblé n'est inventé : il sera mesuré sur un futur
changement produit isolé de `constellations`.
