# P1 #4209 — Audit de sélection CI : provider-numilog

## Frontières et sources de vérité

Le package `provider-numilog` est une **unité de tests indivisible**. Son manifeste `src/plugins/provider-numilog/cifereca-plugin.json` déclare deux contributions distinctes dans le même paquet :

- `provider.numilog` : bibliothèque privée, droits d'achats et livraison via le Core.
- `provider.numilog.catalog` : catalogue commercial public anonyme, **sans preuve de possession ni offre de téléchargement**.

Le manifeste recommande `drm.ade` mais **ce n'est pas** l'identifiant déclaré `runtime.kobo-adept`. Ne pas inventer de liaison de dépendance entre ces IDs différents. Le runtime ADEPT reste lui-même non audité et déclenche donc toujours le repli exhaustif lorsqu'il change.

Le package `home-page` consomme facultativement les contributions de type `provider.library` ; pour une modification isolée d'un module Numilog, le sélecteur doit donc inclure les **deux suites** `home-page` et `provider-numilog`. La sélection est fondée sur le manifeste et contrôlée par le test du graphe, y compris pour les modules privés, publics et métadonnées.

Un changement du manifeste Numilog, d'un helper de tests, un rename, une suppression ou un changement partagé Core continue de sélectionner le **full**. De même pour toute modification mixte impliquant Baen ou un autre package produit non audité.

Un garde-fou scanne les imports concrets et identifiants déclarés dans les autres packages de plugins : une nouvelle consommation transversale explicite échoue et exige une révision du graphe avant de conserver la sélection à deux suites.

## Résultat

Après intégration, **16 packages audités sur 21**, avec **5/21 toujours soumis au full**. Aucun code Numilog, état de droits, runtime DRM, contrat d'acquisition, gate Core/installation/E2E ou couverture interne d'un package n'est modifié. Mesurer la durée des runs réellement comparables avant de revendiquer un gain temporel.
