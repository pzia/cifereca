# P1 #4209 — audit final du package Kobo

## Contrat plugin vérifié

Le manifeste `provider-kobo` regroupe **deux contributions de même package** :
- `provider.kobo.catalog` de type `catalog.service`, fournisseur de catalogue technique ;
- `provider.kobo` de type `provider.library`, compte privé et acquisitions.

Le contrat requis `catalog.service` par `provider.kobo` est **intra-package**. Le consommateur externe `home-page` lit les contributions `provider.library` via contrat facultatif ; modifier le produit Kobo entraîne donc **les suites intégrales** `provider-kobo` et `home-page`.

Le runtime ADEPT est un autre package, **déjà audité** : le graphe explicite `provider-kobo → runtime-kobo-adept` conserve la fermeture de six suites sur modification du runtime, sans assimiler les identifiants `drm.ade` / `runtime.kobo-adept`. Modifier Kobo seul ne requiert pas la suite runtime, la dépendance étant orientée dans l'autre sens.

## Régression et prudence

Les tests historiques précédemment fondés sur l'hypothèse « Kobo non audité » utilisent désormais **un changement de manifeste Kobo**, qui reste `full` pour une raison réelle et vérifiable. Ne pas fabriquer un nouveau package non audité après qualification du 21e. Un test explicite vérifie que des modifications conjointes de deux packages **audités** produisent leur fermeture unifiée et ne déclenchent pas un repli artificiel.

Replis conservés : changement de manifeste, de helper, renommage, suppression, code partagé Core, modification simultanée avec un manifeste runtime. Un contrôle empêche l'apparition silencieuse de nouveaux imports plugin Kobo dans un autre package.

**21 packages sur 21 audités** après fusion ; aucun test interne au package ne disparaît, aucun gate Core/E2E/installation/packaging ni code d'acquisition, DRM, KVault ou compte Kobo n'est modifié.

## Sortie de #4209

L'audit du graphe peut être déclaré complet après PR CI verte. **La mesure du gain de durée**, distincte du nombre de suites sélectionnées, reste à produire sur runs comparables ; ne pas fermer l'objectif « bénéfice mesuré » sans cette preuve.
