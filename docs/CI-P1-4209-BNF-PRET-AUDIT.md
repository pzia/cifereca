# P1 #4209 — audit CI BnF Prêt

## Contrat observé
Le package `provider-bnf-pret` déclare `provider.bnf-pret` de type `provider.library` dans son manifeste, sans dépendance inter-package déclarée. Le consommateur `home-page` possède un contrat `provider.library` facultatif : il doit toujours être testé lors d'une modification du provider.

Une modification isolée d'un module de `provider-bnf-pret` sélectionne donc les **suites intégrales** `provider-bnf-pret` et `home-page`, incluant les tests d'emprunt, de vérification BnF, de sélection et d'ACSM déjà présents dans le package. Aucun test du paquet n'est omis et aucun contrat de mutation n'est modifié.

La recherche de références de modules `cifereca_bnf_pret` n'a pas identifié d'import depuis un autre package plugin. Le test d'audit interdit une consommation inter-package concrète nouvelle sans révision du graphe.

## Replis
Un changement de manifest, de helper, une suppression, un rename ou un changement mixte impliquant Baen non audité ou du Core partagé conserve le **full**. Les autres gates PR CI Core, E2E, installation et packaging restent inchangés.

Après merge : **18 packages audités sur 21**, **3 non audités** (Baen, provider-kobo, runtime-kobo-adept). La durée CI économisée n'est pas démontrée : la mesure comparative reste requise.

## Hors périmètre
Aucun changement du fournisseur BnF, du POST d'emprunt, de l'anti-rejeu, des liens ACSM, d'ADEPT ou des permissions.
