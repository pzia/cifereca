# Numilog — pagination de recherche strictement bornée (0.1.49)

Le fournisseur privé Numilog accepte `page` uniquement si `type(page) is int`
(et donc **pas** `bool`) dans l'intervalle inclusif `0..100`.
Les scopes `all` et `digital` continuent de chercher **les achats possédés** :
leur pagination est une tranche locale de 50 résultats. Le scope `catalog`
reste une recherche commerciale HTTP **anonyme explicite**, isolée du compte ;
sa pagination publique via `GRID_PAGE` reste limitée à la même borne,
déjà imposée par le client HTTP public.

Une page négative, hors borne ou d'un type inattendu provoque
`ProviderCapabilityError("Numilog : invalid_page")` **avant** une lecture
privée de `MesLivres` et **avant** tout GET du catalogue public.
Aucune coercition silencieuse (`int(page)`) ni conversion d'une page
invalide en page 0 ; aucun faux résultat « vide » ne masque l'erreur.

Les deux descripteurs déclarent `max_pages=101` (indices `0..100`) et
conservent respectivement `page_size=50` et `page_size=20`. Ce plafond
est une garde logicielle, **pas** la preuve d'une pagination publique réelle :
seule la première page publique a été documentée hors réseau ; les pages
suivantes, l'accès anonyme live et les conditions du site restent à qualifier.

Régression hors réseau : `tests/test_search_page_p1.py`. Aucun changement de
droit de possession, authentification, acquisition, téléchargement, ni
contrat Core/GTK. Suivi transversal : #1917.
