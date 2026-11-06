# P1 #4209 — audit de graphe CI du provider Paris

## Dépendances observées

Le manifeste `src/plugins/provider-paris/cifereca-plugin.json` déclare la contribution `provider.paris`, type `provider.library`. Il n'importe pas d'autre package plugin via `requires` et n'a pas de `requires_contracts`.

La recommandation facultative `drm.ade` n'est **pas** l'ID déclaré `runtime.kobo-adept`. Ne jamais ajouter un arc supposé pour faire passer l'audit. Le runtime ADEPT demeure lui-même non audité, donc ses modifications de code provoquent toujours le repli exhaustif.

La page `home-page` consomme les fournisseurs de type `provider.library` via son contrat facultatif : elle est le consommateur plugin à retenir, même si la variation concerne un seul module Paris. Une modification isolée de Paris sélectionne **deux suites intégrales** : `home-page` et `provider-paris`.

Les recherches de références inter-packages n'ont identifié aucun import de `cifereca_paris` depuis un autre package plugin. Les mentions de `provider.paris` dans les tests d'inventaire Diagnostics sont des **données synthétiques**, sans import ni dépendance au code du plugin Paris. Un test interdit les futurs imports concrets inter-packages sans mise à jour de cet audit.

## Refus conservateurs

Une modification d'un manifeste, d'un helper de tests, une suppression ou un renommage, un changement mixte avec un package non audité ou du Core partagé maintient le **full**. Les gates **Core, E2E, packaging et installation** ne sont pas réduits. Les tests spécifiques Paris couvrent les parcours PNB/ACSM : tout le package Paris reste exécuté, sans filtrage par module.

## Bilan

Après merge, **17/21 packages audités**, 4 non audités. Le gain temporel n'est pas quantifié ; mesurer les temps CI réels comparables avant d'en annoncer un. Aucun code Paris, prêt, acquisition, droits ou DRM n'est modifié.
