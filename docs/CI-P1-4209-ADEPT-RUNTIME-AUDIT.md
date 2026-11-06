# P1 #4209 — audit CI du runtime Kobo-ADEPT

## Risque et décision conservatrice

Le package `runtime-kobo-adept` regroupe trois contributions **inséparables** dans un seul manifeste : adaptateur `runtime.kobo-adept`, page `ui.page.adept-profiles` et diagnostic `diagnostics.kobo-adept`. Les deux derniers ont un `requires` interne à ce package.

Les manifests Kobo, Paris et Numilog déclarent `recommends: drm.ade`, tandis que l'adaptateur déclare `runtime.kobo-adept` : **ces IDs ne sont pas assimilés**. Néanmoins, les chemins d'acquisition et de découverte de capacités du Core peuvent faire dépendre leurs actions du runtime ADEPT. La page Diagnostics inspecte également l'inventaire/runtime. Le graphe explicite de tests ajoute donc, **par prudence**, des arcs :
- `provider-kobo → runtime-kobo-adept`
- `provider-paris → runtime-kobo-adept`
- `provider-numilog → runtime-kobo-adept`
- `diagnostics-page → runtime-kobo-adept`

Le contrat facultatif `home-page → provider.library` ajoute un consommateur transitif. Une modification isolée du runtime exécute **six suites entières** :
`runtime-kobo-adept`, `provider-kobo`, `provider-paris`, `provider-numilog`, `diagnostics-page`, `home-page`.

C'est une couverture supplémentaire à la stricte lecture des manifests, pas une déclaration d'équivalence des plugins ou une autorisation de DRM. Le transport runtime et son exécutable système restent soumis aux tests du package, les gates Core/E2E/installation demeurent inchangés.

## Repli et limites

Les changements de manifeste, de helper, les renames/suppressions, les changements partagés et les changements mixtes avec le package encore non audité `provider-kobo` gardent le **full**. Tout nouvel appel direct inter-package doit entraîner un nouvel audit des dépendants.

Après intégration : **20/21 packages audités**, `provider-kobo` reste non audité et déclenche le full sur ses modifications. **Aucune réduction de tests de sécurité ACSM, ADEPT ou prêts**, aucune modification du code opérationnel ou de la configuration utilisateur.

La durée CI des parcours ciblés vs complets reste à mesurer sur des runs comparables : aucune économie temporelle chiffrée n'est établie.
