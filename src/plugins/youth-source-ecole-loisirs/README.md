# Jeunesse — l'école des loisirs

Source spécialisée francophone pour #741.

La recherche publique avancée de **l'école des loisirs** publie directement, dans chaque résultat, une tranche d'âge et un ISBN : `0 à 3 ans`, `3 à 6 ans`, `6 à 8 ans`, `8 à 11 ans`, `11 à 13 ans`, `13 ans et +`, `à partir de X ans`, ou `indéterminé`.

## Règles

- aucune tranche n'est inférée depuis collection, genre ou résumé ;
- `indéterminé` reste sans `age_ranges` ;
- ISBN-13 validé avant publication ;
- URL produit conservée comme notice publique ;
- type éditorial simple conservé lorsqu'il est explicitement visible ;
- aucune page produit n'est nécessaire pour qualifier l'âge.

## Recherche et pagination

Le plugin utilise seulement `/recherche-avancee` avec `types=livre` et le champ public `search_api_views_fulltext`.

La pagination n'est proposée que si la page contient explicitement un lien **Afficher plus de résultats** vers une page suivante. Aucun crawl complet n'est déclenché.

## Réseau

- HTTPS uniquement ;
- hôte `www.ecoledesloisirs.fr` uniquement ;
- chemin de recherche avancée uniquement pour les requêtes réseau ;
- redirections et proxy d'environnement désactivés ;
- HTML limité à 2 Mo ;
- timeout borné.


## Durcissements 0.2.0

- une tranche d'âge n'est plus recherchée au-delà de la précédente carte produit : aucune propagation accidentelle entre notices ;
- la pagination accepte uniquement la page immédiatement suivante publiée par « Afficher plus de résultats » ;
- requête texte normalisée et bornée à 300 caractères ;
- réponses HTTP 3xx rejetées ;
- `script`, `style`, `noscript` et `svg` restent exclus du parsing ;
- les règles ISBN, hôte HTTPS, ports et absence d'inférence restent inchangées.
