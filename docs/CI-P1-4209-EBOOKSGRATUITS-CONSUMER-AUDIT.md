# Audit CI P1 #4209 — ebooksgratuits (05/11/2026)

## Décision

Qualifier le package `ebooksgratuits` comme **dixième package audité sur 21**, avec dépendance explicite `home-page → ebooksgratuits`. Une modification isolée de ce fournisseur sélectionne les **deux suites plugins** `ebooksgratuits` et `home-page`. Les **11 autres packages non audités** gardent le repli exhaustif.

## Preuves

- Manifest `provider.ebooksgratuits` : `requires transport.http` (package `transport-http`), aucun `recommends` ou `requires_contracts`. Le graphe manifeste couvre déjà le transport ; sa modification ferme donc vers `ebooksgratuits` puis `home-page`.
- Le module `cifereca_ebooksgratuits.py` utilise le contrat Core public et la bibliothèque Python, sans import direct d'un autre plugin produit.
- `home-page/cifereca_home.py` classe `ebooksgratuits` dans `PUBLIC_PROVIDERS`, `SOURCE_NAMES` et `_NON_LOAN_PROVIDERS`. C'est un couplage produit effectif absent des manifestes. Il justifie une dépendance explicite dans le graphe du sélecteur.
- Aucun autre package plugin ne référence les IDs ou modules spécifiques en code ou manifests (test de garde). Les consommations dans Core, les tests transverses et les simulations restent couvertes par les gates indépendants inchangés.

## Garanties

- Changement isolé du module fournisseur → suites `src/plugins/ebooksgratuits` + `src/plugins/home-page`, **2/21**.
- Changement `transport-http` → fermeture transitive vers le fournisseur puis la page.
- Changement de `home-page` non audité, de Baen non audité, manifest, helper partagé, suppression, renommage ou mélange avec un package non audité → exécution exhaustive **21/21**.
- Aucun changement métier, de transport, de droits, de test existant ou de gate Core/E2E/installation. Les gains de durée doivent être mesurés sur runs comparables.
