# Jeunesse — Éditions Milan

Source spécialisée pour #741.

Le catalogue public Milan est paginé et les fiches produit publient explicitement l'âge et le code ISBN. Exemples observés : `À partir de 1 ans`, `À partir de 8 ans`, `À partir de 11 ans`.

## Stratégie

La recherche textuelle utilise désormais la route publique attestée `/recherche/books/?text=...`. La navigation sans recherche conserve le catalogue historique ; un curseur numérique explicite maintient la compatibilité avec une collecte locale déjà commencée.

1. interroge `/recherche/books/?text=...` pour une recherche, ou `/livres/` et `/livres/page/N/` sans recherche ;
2. extrait les liens produit ;
3. ne refiltre **pas** par titre les résultats de la recherche distante (la recherche peut porter sur d'autres attributs) ; conserve le filtrage local pour les anciens curseurs numériques ;
4. ouvre au plus 8 fiches produit ;
5. ne publie le résultat que si une tranche d'âge explicite **et** un ISBN valide sont présents, dédoublonne par édition/ISBN et expose couverture publique, auteur, illustrateur, thèmes, description et caractéristiques attestées.

La pagination est reprise uniquement lorsqu'un lien public vers la page suivante est effectivement présent. Un curseur de recherche est l'URL publiée, validée sur l'hôte, la requête et le numéro de page. Aucun paramètre de pagination n'est inventé.

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
- requêtes exclusivement sur `editionsmilan.com` ; couvertures HTTPS publiques autorisées sur `imagine.bayard.io` sans téléchargement par le plugin ;
- chemins de catalogue, recherche ou fiche produit uniquement ;
- redirections désactivées ;
- proxy d'environnement désactivé ;
- réponses HTML limitées à 2 Mo ;
- timeout borné ;
- 8 fiches produit maximum qualifiées par appel ;
- aucune collecte implicite.

## Validation source → catalogue (P1 #813)

La collecte Milan refuse un âge hors **0–17** ou non numérique **avant tout appel HTTP**, même lorsque la page de catalogue est vide. La recette plugin déterministe exerce le vrai `MilanYouthSource.browse`, l'entrée âge-aware `fetch_source_page` de la page Jeunesse, puis l'index `YouthCatalogPlugin`, sur un client HTTP simulé sans réseau extérieur. Elle couvre la pagination, la conservation d'ISBN et provenance, l'indisponibilité 503/timeout et une reprise explicite ; la lecture du cache n'active pas de requête réseau.

Ces tests techniques ne prouvent ni l'accessibilité actuelle du site public Milan ni un parcours utilisateur GTK avec source activée : ces points relèvent de la validation dédiée et de la recette UX indépendante.

## État #946 (P2)

Les tests de recherche/fiche utilisent des exemples HTML **synthétiques** alignés sur les champs rapportés le 10/10/2026. Ce ne sont pas des captures brutes ou des fixtures HTML certifiées du site : la confrontation au DOM réel reste à réaliser avec les captures de source expurgées. La normalisation commune `YouthBook` conserve notamment couverture et année, mais **ne conserve pas encore la totalité de `metadata`, `description` et `illustrators`** ; voir la dépendance Core/détail #942. Ne pas déclarer cette recette bout-en-bout close.
