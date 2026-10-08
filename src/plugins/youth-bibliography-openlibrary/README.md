# Jeunesse — Open Library

Résolveur bibliographique complémentaire pour #741.

Open Library fournit des APIs publiques de recherche et de lookup adaptées aux requêtes humaines à faible volume. Ce plugin utilise `https://openlibrary.org/search.json` pour les recherches/ISBN et les endpoints JSON documentés `/works/<OLID>.json` ou `/books/<OLID>.json` pour les identifiants Open Library. Il ne scrape aucune page HTML.

## Frontière d'âge

Open Library est utilisé ici comme **enrichissement bibliographique uniquement**.

Chaque résultat porte explicitement :

- `bibliographic_only = true` ;
- `age_evidence = none` ;
- `age_ranges = ()`.

Les sujets, langues ou collections Open Library ne sont jamais transformés en tranche d'âge.

## Contrat

Catégorie : `youth.bibliography` / `cifereca.youth-bibliography/1`.

Le service expose :

- `lookup_isbn(isbn)` ;
- `lookup_olid(olid)` ;
- `search(query, limit=10, page=1, language="")` ;
- `source_info()`.

Les résultats conservent titre, auteurs, ISBN, langues, sujets, éditeurs, éditions Open Library, première année de publication, notice et couverture.

## Usage réseau

- maximum 20 résultats par appel ;
- page bornée à 100 ;
- réponses JSON bornées à 2 Mo ;
- cache mémoire LRU ;
- 1 requête/seconde par défaut ;
- 3 requêtes/seconde lorsque le constructeur reçoit une adresse de contact valide ;
- aucun crawl, aucun bulk import, aucun appel implicite au simple affichage.

Ces limites suivent les recommandations publiques d'Open Library pour les APIs à faible volume.
