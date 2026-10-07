# Socle commun des jeux de données

`fake_books.py` centralise ce qui ne dépend d'aucun fournisseur :

- vocabulaire et génération déterministe des titres ;
- auteurs, éditeurs, sujets, langues et descriptions ;
- couverture PNG sans dépendance externe ;
- EPUB 3 minimal valide avec couverture et premier chapitre.

Chaque simulateur utilise un `namespace` distinct avec la même graine. Les
protocoles, états métier, identifiants et caches restent propres à chaque
service (OPDS, Kobo, Paris, Numilog).
