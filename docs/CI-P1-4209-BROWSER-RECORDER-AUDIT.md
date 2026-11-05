# Audit CI P1 #4209 — browser-recorder (05/11/2026)

## Décision

Ajouter uniquement le package `browser-recorder` aux packages audités : **6/21**. Les quinze autres packages restent non audités et leur modification de code déclenche la totalité des suites plugins. Les gates Core, E2E, Chromium, packaging et installation ne changent pas.

## Dépendances établies

- Manifest `src/plugins/browser-recorder/cifereca-plugin.json` : contribution `ui.page.browser-recorder` de type `ui.page`, **aucun** `requires`, `recommends` ni `requires_contracts`. Le broker `tasks` est public/Core ; `playwright` et `cryptography` sont des `system_requires`, non des plugins internes.
- La page utilise les modules de son propre package (`browser_recorder*.py`) et le contrat public `cifereca.plugin_api.UiPageContribution`. Les extracteurs ZIP, revue, export et coffre ne requièrent pas de service fourni par un autre package plugin.
- Les autres manifests de ce dépôt ne déclarent pas `ui.page.browser-recorder` comme dépendance. Les références à `browser_recorder` dans les tests de `diagnostics-page` concernent **une valeur de domaine diagnostique** et ne constituent pas un import d'un module Recorder. Les imports et IDs spécifiques sont surveillés par le test `test_browser_recorder_modules_have_no_other_plugin_consumers`.
- Les tests hors package du Recorder sont **Core**, installation ou **E2E Chromium** : ils ne sont pas exclus par le ciblage des seules *suites plugins*. Les contrôles spécifiques de capture, confidentialités et consentement restent soumis aux gates inchangés.

## Gardes de non-régression

- Changement produit isolé `browser_recorder.py`, page, ZIP ou coffre → ciblage `src/plugins/browser-recorder` (1/21).
- Tout nouveau consommateur déclaré entre packages devient un arc du graphe. Une référence directe à ses modules / IDs spécifiques dans un autre package fait échouer un test, forçant un réaudit.
- Manifest, helpers partagés, suppression, renommage, changement produit mixte avec un package non audité (ex. Baen) → **full 21/21**.
- Aucune promotion implicite des autres packages ou des capacités optionnelles ; aucune réduction des contrôles Core/E2E/Chromium.

Le gain de durée n'est pas revendiqué sans mesure sur des runs comparables.
