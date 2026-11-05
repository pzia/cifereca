# Audit CI P1 #4209 — provider-gutenberg (05/11/2026)

## Décision

Qualifier uniquement `provider-gutenberg` comme huitième package de produit audité (**8/21**). Le changement isolé de ce fournisseur exécute **deux** suites plugins : `provider-gutenberg` et `home-page`, pas uniquement Gutenberg. Les **13** packages non audités conservent le repli exhaustif.

## Dépendances et consommateurs

- Manifest : `provider.gutenberg` de catégorie `provider.library`, dépendance requise `transport.http` (package `transport-http`). Le moteur de fermeture existant suit déjà cette dépendance ; la présente tranche ne modifie pas le sélecteur.
- Source `cifereca_gutenberg.py` : dépendances du même package et Core public (`cifereca.plugin_api`), sans import d'un autre package produit. `transport-http` est un fournisseur de capacité, pas un consommateur du plugin Gutenberg : modifier Gutenberg n'entraîne pas la suite transport.
- `home-page/cifereca_home.py` utilise explicitement le fournisseur `gutenberg` pour `PUBLIC_PROVIDERS`, `SOURCE_NAMES` et `_NON_LOAN_PROVIDERS`. Ajouter l'arc explicite `home-page → provider-gutenberg` à `.github/plugin-test-dependencies.json`, à côté de l'arc BnF.
- Les références `gutenberg` dans `recommendations/README.md` sont documentaires : pas d'import ni appel de capacité spécifique par ce package. Le test de garde parcourt le code, les tests et les manifests des autres packages, sans lire les README.

## Contrats des tests

- Changement produit isolé de Gutenberg → `home-page` + `provider-gutenberg` (2/21).
- Changement de `transport-http` → la fermeture transitive couvre Gutenberg et sa page consommatrice, ainsi que les autres fournisseurs déclarés.
- Changement produit de `home-page` non audité → gate exhaustif (21/21).
- Tout changement mixte avec Baen non audité, manifest, helper partagé, suppression ou rename → gate exhaustif.
- Nouvelle référence de code à Gutenberg dans un autre package → échec du garde et réaudit requis.

Aucun changement du produit ou du flux réseau, aucune suppression de test. Gates Core, E2E, packaging et installation inchangés. Une réduction théorique de 21 à 2 suites ne prouve pas un gain mesuré en secondes.
