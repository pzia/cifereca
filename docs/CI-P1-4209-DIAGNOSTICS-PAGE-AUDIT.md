# Audit CI P1 #4209 — diagnostics-page (05/11/2026)

## Décision

Ajouter uniquement `diagnostics-page` aux packages produits audités : **5/21** au total. Les 16 autres packages conservent la sélection exhaustive en cas de changement produit ; pas de modification des gates Core, E2E, packaging ou installation.

## Preuves et périmètre

- Manifest `src/plugins/diagnostics-page/cifereca-plugin.json` : une seule contribution `ui.page.diagnostics` de type `ui.page`, sans `requires`, `recommends` ou `requires_contracts` ; les dépendances de services Core passent par les API publiques.
- Les modules de la page (`cifereca_diagnostics_page.py`, `diagnostics_explorer.py`, `diagnostics_ai.py`, `diagnostics_support.py`, etc.) utilisent des modules du **même** package et les contrats Core (`cifereca.plugin_api`). Les appels LLM restent des appels au broker Core, pas à un autre plugin produit.
- Aucun manifest de plugin ne déclare de consommateur de cette contribution particulière via `requires` ou `requires_contracts`. Une dépendance future par ID ou catégorie devient un arc dans le sélecteur ; les références directes à ses modules/ID par un autre package sont gardées par un test dédié.
- Les diagnostics possèdent aussi des tests **Core** et **E2E** hors du package ; ces gates restent inchangés. Un ciblage du runner de tests *plugins* ne dispense pas des recettes E2E, des tests Core ni des contrôles de sécurité.

## Invariants vérifiés par les tests

- Modification isolée des quatre modules représentatifs → une seule suite plugin `src/plugins/diagnostics-page` (1/21).
- Modification de `baen` non audité, seule ou mixte → gate plugin exhaustif (21/21).
- Manifest, helper partagé, suppression et renommage → gate exhaustif.
- Nouveau consommateur via import/module/ID dans un autre package → test de garde en échec, réaudit obligatoire ; dépendances déclarées → fermeture transitive automatique.

## Limite

Il ne s'agit **pas** d'une preuve que tous les consommateurs hors runner plugins peuvent être ignorés. Les tests Core/E2E restent indépendamment requis. Mesurer la durée effective sur une modification produit isolée avant de revendiquer une accélération observée.
