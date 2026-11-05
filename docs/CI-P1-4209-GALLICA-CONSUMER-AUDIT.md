# Audit CI P1 #4209 — provider-gallica (05/11/2026)

## Décision

Qualifier le package `provider-gallica` comme **neuvième package audité sur 21**, en ajoutant l'arc explicite `home-page → provider-gallica`. Une modification isolée de Gallica sélectionne **deux suites plugins**, Gallica et home-page. Les **12 packages non audités** conservent le repli exhaustif.

## Preuves

- Manifest `provider.gallica` : `requires: [{plugin: transport.http}]`, sans `recommends` ni `requires_contracts`. La fermeture transitive `transport-http → provider-gallica → home-page` est testée.
- `home-page/cifereca_home.py` inclut la source `gallica` dans `PUBLIC_PROVIDERS`, `SOURCE_NAMES` et `_NON_LOAN_PROVIDERS`; cela constitue une consommation de classification produit, même sans dépendance manifeste.
- Les modules `cifereca_gallica.py` et `cifereca_gallica_audio.py` appartiennent au même package. Le test de garde refuse de nouveaux imports/IDs spécifiques Gallica dans les autres packages plugins.
- `provider-bnf-catalogue` contient une extraction de **liens HTTP Gallica** depuis des métadonnées BnF, et non un import de `provider.gallica` ou de ses modules : aucun arc d'exécution supplémentaire inventé. Les éventuelles régressions bibliographiques restent dans les tests propres au catalogue BnF et les gates Core/E2E inchangés.

## Invariants

- Modification isolée de code Gallica → suites `src/plugins/provider-gallica` et `src/plugins/home-page`.
- Changement de `transport-http` → consommateurs transitifs dont Gallica et home-page.
- Changement de `home-page` (non audité), de Baen, manifest, helper partagé, suppression, renommage ou mélange avec un package non audité → gate **full 21/21**.
- Aucune suppression de tests, modification métier, nouveau flux réseau ni assouplissement des gates Core/E2E/installation. Gain de durée à mesurer sur runs comparables.
