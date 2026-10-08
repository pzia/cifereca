# Jeunesse — BookTrust

Source spécialisée pour le plugin Jeunesse.

BookTrust Bookfinder publie des recommandations jeunesse et, sur les fiches livre, des champs distincts `Interest age` et `Reading age`. Le plugin conserve ces valeurs explicites sans les déduire du texte descriptif.

## Comportement

- `browse(age=..., themes=..., cursor=...)` charge une page Bookfinder explicitement demandée ;
- `search(query, ...)` filtre localement cette page : aucune API de recherche texte BookTrust non attestée n’est inventée ;
- `detail(url)` accepte uniquement une URL HTTPS BookTrust Bookfinder et extrait les âges d’intérêt/lecture publiés ;
- pas de crawl complet ;
- pagination explicite ;
- cache demandé au niveau de l’agrégateur Jeunesse ;
- réponse HTML limitée à 2 Mo ;
- aucun suivi de redirection inter-domaine.

Les tranches Bookfinder actuellement publiées vont de 0–1 à 11–14 ans. Pour un âge non couvert par ces groupes, le plugin n’invente aucun filtre.

## Intégration

Le plugin expose le contrat structurel attendu par `cifereca.youth-source/1` : `source_info()` et `search()`. Les lignes retournées sont directement normalisables par `youth-catalog`.
