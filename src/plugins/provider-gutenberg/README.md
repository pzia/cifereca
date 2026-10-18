# Provider Project Gutenberg — provenance des dates OPDS

## Audit des métadonnées P2 #764 — 11 octobre 2026

**Constat confirmé dans le code Cifereca :** `_parse_feed` affectait
`atom:published or atom:updated` à `SearchResult.published_at`.
`ItemDetail.year` réutilisait cette date, créant un risque de faux millésime
pour l'ouvrage lorsque la notice avait simplement été mise à jour.

**Preuve amont publique :**
- Project Gutenberg distingue explicitement sur la [fiche du livre nº 11,
  Alice's Adventures in Wonderland](https://www.gutenberg.org/ebooks/11)
  la **release date** 27 juin 2008 et la **last update** 26 juin 2025.
  La page publique mentionne par ailleurs 1865 comme première publication
  historique de l'œuvre, ce qui est encore une troisième notion.
- Dans les [catalogues hors ligne et OPDS](https://www.gutenberg.org/ebooks/offline_catalogs.html),
  Gutenberg indique ne pas intégrer les dates d'éditions imprimées originales
  dans son catalogue machine, précise que les ebooks peuvent dériver de
  différentes éditions, et annonce une transition des flux XML OPDS
  vers OPDS2 JSON avec arrêt attendu en **2027**.
- La [spécification OPDS 1.2](https://specs.opds.io/opds-1.2.html)
  différencie `atom:updated` (dernière mise à jour de la notice),
  `atom:published` (première mise à disposition de la notice) et
  `dcterms:issued` (publication de l'objet bibliographique).

**Projection après correction (1.2.18) :**
- `SearchResult.published_at` et `ItemDetail.year` ne reçoivent que
  `dcterms:issued` lorsque le champ existe et contient une année/date ISO
  valable. Une date inconnue reste `None`.
- `metadata.opds_entry_published` et `metadata.opds_entry_updated`
  gardent séparément les horodatages Atom valides, sans prétendre être des
  dates de publication imprimée ou des âges recommandés.
- `metadata.publication_date_evidence = opds.dcterms.issued` atteste
  explicitement le champ exploité. Aucune consultation réseau complémentaire,
  aucun appel à un LLM, aucun rapprochement d'éditions ni écrasement d'âge.
- Les dates de précision **année** (`1865`), **mois** (`2008-06`) et
  **jour** (`2008-06-27`) sont conservées telles quelles. Une chaîne
  non-ISO ou un jour impossible n'est pas promu en preuve.

**Couverture des tests :** `tests/test_opds_date_provenance_p2.py`
contient des exemples OPDS **synthétiques**, et non une capture XML brute
réelle. Les preuves d'amont proviennent de la page Gutenberg et des specs.
La validation d'un flux OPDS brut reste une recette séparée lorsque
les règles d'accès et de fréquence Gutenberg le permettent.

**Limite 2027 :** la publication d'OPDS2 JSON est annoncée comme
disponible pour test sur demande ; aucun endpoint OPDS2 n'est inventé
par cet incrément. Une migration spécifique doit être qualifiée
auprès de Gutenberg avant l'extinction éventuelle d'OPDS XML.


## Contrat strict des index de page OPDS (#1917, 1.2.19)

La pagination par lien `rel=next` reste bornée à 101 pages, indexées
`0..100`. Le provider refuse désormais toute valeur `page` non entière
au sens strict (booléens, chaînes, nombres flottants), négative ou
supérieure à 100 avec `ProviderCapabilityError('Project Gutenberg : invalid_page')`,
avant d'envoyer une requête. Il ne convertit plus les pages négatives en zéro.
Une erreur de page efface les avertissements de recherche précédents.
Cela n'ajoute aucun GET et ne prétend pas garantir que 101 pages existent
côté fournisseur.
