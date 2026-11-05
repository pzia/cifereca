# Audit CI P1 #4209 — epub_optimizer (05/11/2026)

## Décision

Qualifier `epub_optimizer` comme **treizième package audité sur 21** si la CI du HEAD est verte puis fusionnée. Les huit packages non audités continuent à déclencher le contrôle exhaustif.

## Preuves de fermeture

- Manifest du package : deux contributions `document.transformer` EPUB/PDF, quatre `device.profile` (Xteink X3, X4, Kobo Clara BW et générique) et une contribution `ui.page.epub-optimizer`.
- Les transformations déclarent un `requires_contracts` requis sur la catégorie `device.profile`, dont les producteurs déclarés dans le dépôt sont contenus dans **le même package**. La page exige les deux IDs transformeurs, également intra-package. Le graphe `consumer → dependency` ferme donc en interne.
- Les recherches dans les autres packages plugins sur les identifiants de contributions et les imports `cifereca_epub_optimizer` / `cifereca_pdf_optimizer` ne montrent pas de consommateur interpackages. Un test parcourt leurs fichiers exécutables/manifests pour déceler un nouveau consommateur.
- Les appels Core au broker de transformation et les contrats matériels de profils liseuse restent couverts par les gates Core et d'installation existants ; une suite de package CI n'équivaut pas à la suppression de ces gates.

## Tests et repli conservateur

- Modification isolée de modules du package (transformers, profiles, page) → suite `src/plugins/epub_optimizer` (1/21).
- Changement d'un package non audité (Baen), mélange avec Baen, manifest, helper partagé, suppression ou renommage → gate complet (21/21).
- Une nouvelle consommation explicite d'un ID/profil ou d'un module par un autre package fait échouer le test de garde et oblige à réauditer la fermeture.

Aucune modification de code produit, de règles DRM, de provenance ou de contrat d'acquisition ; aucune baisse des contrôles Core/E2E/installation. Les gains de durée n'ont pas été mesurés sur runs comparables.
