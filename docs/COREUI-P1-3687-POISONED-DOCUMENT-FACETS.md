# CoreUI P1 #3687 — Facettes documentaires locales et notices défectueuses

Les vues Recherche, Bibliothèque et Recommandations utilisent le même composant `LocalQueryControls` pour décrire les supports et formats des éléments **déjà chargés**. Le parseur générique `loaded_document_facets` ne doit jamais faire échouer toute la barre GTK lorsqu'un fournisseur restitue un format opaque, un champ de métadonnées qui lève une exception ou une classification illisible.

Désormais, une erreur d'extraction ne touche que la **notice fautive** : `("unknown", ())`. Une notice voisine saine continue de proposer ses formats et son support. Une classification explicite `unknown` reste prioritaire par rapport à un champ legacy `format`/ `medium` contradictoire : aucune inférence de fichier, de droit ou de provenance n'est faite. Le message d'exception, potentiellement porteur de chemins locaux ou de secrets, n'est jamais repris dans le rendu.

**Recette :** tests purs Core `test_core_p1_3687_poisoned_document_facets.py` (métadonnées malformées, source voisine valide, classification typée inconnue) et GTK/XTEST `search-filter-p0` (vraie barre locale, apparition EPUB, filtrage sans faux positif sur la notice opaque, disparition d'une facette redevenue vide, Recherche globale inchangée). Aucun GET, POST, rescan de bibliothèque ou traitement de droits induit.
