# P1 #4640 — Kobo K1 : comparaison fermée des candidats admissibles

Le relevé du 06/11/2026 constate **deux candidats admissibles** pour K1 sans prouver leur équivalence. Le diagnostic existant `candidate_shape_counts` décrit leurs catégories de format/source, mais ne distingue pas deux URL signées distinctes pour une même route.

Sur la réponse déjà obtenue, uniquement si **plusieurs candidats demeurent admissibles**, le provider ajoute `candidate_route_comparison` au journal expurgé :

| Valeur | Fait établi | Conséquence |
| --- | --- | --- |
| `distinct_routes` | Plusieurs contrats `kind/handling/DRM/format/source` | Aucun choix |
| `same_route_distinct_urls` | Route identique, **URL signées différentes** | Aucun choix |
| `same_route_same_url` | Route et URL signée identiques | Aucun choix : validité/entitlement peuvent différer |

La comparaison des URL ne sort pas de la mémoire locale : **ni URL, ni empreinte, ni jeton, ni identifiant privé ne sont inscrits dans le champ diagnostic**. La préférence existante pour une route explicitement non legacy reste prioritaire, et seule la liste finale des candidats admissibles est comparée. Les cas sans candidat et les erreurs de scope demeurent inchangés.

Tests synthétiques des trois situations, test de journal du provider avec URL contenant un marqueur secret, refus de transfert de droits et conservation de `unknown` pour K1. Aucun GET supplémentaire, téléchargement, synchronisation massive ni modification de l'admission KDRM. K2 (média `unknown` pour source `flepub`) et K3 (2 refus) sont à qualifier séparément, notamment par les compteurs et le contrat effectif de la version KVault déployée.

Cette branche est reconstruite **sur le dernier main** afin de préserver le diagnostic `candidate_shape_counts` ajouté par d'autres travaux : elle remplace la proposition plus ancienne de [#4723](https://github.com/pzia/cifereca/pull/4723), sans prétendre avoir validé l'équivalence fournisseur.
