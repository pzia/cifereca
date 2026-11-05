# Audit CI P1 #4209 — provider-bnf-catalogue et consommateur home-page (05/11/2026)

## Décision et limite

Qualifer seulement `provider-bnf-catalogue` comme septième package produit audité (**7/21**). **14 packages** restent non audités et provoquent le repli exhaustif pour tout changement produit. Contrairement à un package réellement isolé, une modification du catalogue BnF doit exécuter **deux suites plugins** : `provider-bnf-catalogue` et `home-page` (2/21). Ne jamais optimiser en omettant ce consommateur.

## Preuves

- Le manifest BnF publie un `provider.library` (`provider.bnf-catalogue`), sans `requires`, `recommends` ou `requires_contracts`. Son module importe `httpx`, `cifereca.plugin_api.HTTPX_TRANSPORT` (Core) et des modules Python standard ; pas de module produit d'un autre plugin.
- `src/plugins/home-page/cifereca_home.py` référence explicitement le fournisseur `bnf-catalogue` dans `PUBLIC_PROVIDERS`, `SOURCE_NAMES` et `_NON_LOAN_PROVIDERS` : classification publique, libellé et état de prêt. Ce couplage ne figure pas dans les manifests : ajout conservatif `dependencies["home-page"] = ["provider-bnf-catalogue"]`.
- Aucune autre référence exécutable à ses modules / IDs spécifiques dans un autre package plugin identifiée dans la revue. Le test `test_bnf_catalogue_specific_cross_package_consumers_are_known` parcourt les sources et manifests des autres packages et refuse tout nouveau consommateur non représenté dans le graphe.
- Les références Core, scripts de simulation, diagnostics et tests transverses restent couvertes par les **gates Core/E2E/packaging/installation existants**, inchangés. Ce ciblage ne réduit que le sous-ensemble des suites de packages plugins.

## Contrats testés

- Changement isolé du module BnF : fermeture explicite = `home-page` + `provider-bnf-catalogue` (2/21).
- Changement du code `home-page` : non audité, repli exhaustif (21/21).
- Changement de `baen` non audité, seul ou mixte : repli exhaustif.
- Manifest, helper de test partagé, suppression ou renommage : repli exhaustif.
- Nouvelle référence interpackages non documentée : échec du garde et réaudit requis.

Aucune suppression de tests, aucune nouvelle permission, aucune modification du produit. Les durées et gains CI doivent être mesurés sur des exécutions comparables ; la réduction théorique 21→2 suites ne prouve pas un gain réel en secondes.
