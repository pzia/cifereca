# Jeunesse — Éditions Milan

Source spécialisée pour #741.

Le catalogue public Milan est paginé et les fiches produit publient explicitement l'âge et le code ISBN. Exemples observés : `À partir de 1 ans`, `À partir de 8 ans`, `À partir de 11 ans`.

## Stratégie

Le plugin n'invente aucun paramètre de recherche distant.

1. charge une page publique `/livres/` ou `/livres/page/N/` ;
2. extrait les liens produit ;
3. applique le filtre texte **localement** sur les titres de cette page ;
4. ouvre au plus 8 fiches produit ;
5. ne publie le résultat que si une tranche d'âge explicite **et** un ISBN valide sont présents.

La pagination est reprise uniquement lorsqu'un lien public vers la page suivante est effectivement présent.

## Âge

`À partir de N ans` est projeté en :

- `age_min=N` ;
- `age_max=None` ;
- `age_kind=publisher` ;
- `source_label` inchangé ;
- confiance 1,0.

Les valeurs >= 18 ans sont hors périmètre Jeunesse et rejetées.

Aucun âge n'est déduit de la collection (`Littérature 9-12 ans`, etc.), du résumé, des thèmes ou du titre.

## Réseau

- HTTPS uniquement ;
- domaine `editionsmilan.com` uniquement ;
- chemins catalogue / produit uniquement ;
- redirections désactivées ;
- proxy d'environnement désactivé ;
- réponses HTML limitées à 2 Mo ;
- timeout borné ;
- 8 fiches produit maximum qualifiées par appel ;
- aucune collecte implicite.

## Validation source → catalogue (P1 #813)

La collecte Milan refuse un âge hors **0–17** ou non numérique **avant tout appel HTTP**, même lorsque la page de catalogue est vide. La recette plugin déterministe exerce le vrai `MilanYouthSource.browse`, l'entrée âge-aware `fetch_source_page` de la page Jeunesse, puis l'index `YouthCatalogPlugin`, sur un client HTTP simulé sans réseau extérieur. Elle couvre la pagination, la conservation d'ISBN et provenance, l'indisponibilité 503/timeout et une reprise explicite ; la lecture du cache n'active pas de requête réseau.

Ces tests techniques ne prouvent ni l'accessibilité actuelle du site public Milan ni un parcours utilisateur GTK avec source activée : ces points relèvent de la validation dédiée et de la recette UX indépendante.
