# P1 #4640 — Kobo K1 : classification expurgée de deux offres admissibles

Le relevé utilisateur du 06/11/2026 établit **K1 : 2 candidats / 2 admissibles**, mais ne démontre pas si les deux offres sont équivalentes. Un même opaque `delivery_offer_id` ne constitue pas un justificatif d'équivalence : les liens signés et les conditions d'exécution peuvent différer.

## Diagnostic ajouté

Sur les **seuls candidats admissibles**, lors du parcours de préqualification existant et sans nouveau GET, `candidate_route_comparison` vaut :

| Valeur fermée | Signification | Sélection autorisée ? |
| --- | --- | --- |
| `distinct_routes` | Plusieurs couples `(kind, handling, DRM, format/source)` | **Non** |
| `same_route_distinct_urls` | Même couple, URLs signées **différentes** | **Non** |
| `same_route_same_url` | Même couple et même URL signée **observée** | **Non** : les autres conditions de validité peuvent différer |

La comparaison des URL n'est faite qu'en mémoire ; **aucune URL, empreinte, signature, identifiant privé ou jeton ne figure dans le diagnostic**. Cette catégorie apparaît uniquement pour au moins deux candidats retenus ; les cas **K2** (1/0, `flepub` non qualifié) et **K3** (2/0, rejets individuels déjà agrégés) ne changent pas. `eligible_count_not_one` et `outcome=unknown` restent invariants jusqu'à preuve et sélection légitime.

## Recette

Tests synthétiques couvrant les trois catégories, l'invariance d'absence de sélection automatique et la suppression des URLs signées des journaux. Une prochaine observation ciblée K1 peut communiquer la seule valeur fermée `candidate_route_comparison` avec `candidate_selection` et `candidate_rejection_counts`, sans requérir de compte, d'URL ni de nouveau journal brut dans GitHub.

Ne pas rapprocher des candidats K1 par simple ISBN, titre, URL similaire ou hash opaque. Le cas K2 exige la valeur brute normalisée/contrat KVault avant toute extension de format, et K3 ses compteurs agrégés ; les trois restent **P1 actifs** tant que la voie exécutable n'est pas qualifiée.
