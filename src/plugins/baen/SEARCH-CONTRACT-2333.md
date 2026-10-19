# #2333 P2 — Contrat de recherche Baen Free Library

Qualification limitée au catalogue **public et opt-in** `provider.baen` :
`enabled_by_default=false` dans `cifereca-plugin.json`.
`My Books` / achats / compte sont **hors périmètre**, ticket #539.
La simulation spécifique conserve son ticket #2332 ; elle ne fait pas partie
de la recette finale de #1917 P1.

| Surface | Comportement du provider | Preuve / réserve |
| --- | --- | --- |
| `field=all` | GET public `/allbooks/category/index/id/2012`, `q` texte général, `hideBundles/hideEarcs` | URL Free Library visible publiquement ; **sémantique serveur exacte de `q` non vérifiée sur HTTP live** |
| `author/title/isbn/subject` | `unsupported_field` avant HTTP | Pas de recherche spécialisée revendiquée. Repli vers `all` seulement **si Core le propose et l'annonce comme global** |
| `scope=all/digital` | Même catalogue public numérique ; autres scopes `unsupported_scope` | Aucun droit de bibliothèque personnelle inféré |
| `page=0..100` | HTTP `page=1..101`, `pageSize=50` ; page invalide `invalid_page` | Compteur/page serveur, pas de crawl de pages voisines |
| Panne réseau / HTTP 5xx | `service_unavailable` | Aucun faux résultat vide |
| HTTP 401 / 403 / 429 | `auth_required` / `access_denied` / `rate_limited` | Pas de contournement ni redirection |
| HTTP 200, au moins une carte publique | Notice sans achat, `owned=false`, `downloadable=false` | HTML des cartes uniquement ; pas d'acquisition pendant search |
| HTTP 200, aucune carte, `Showing 0-0 of 0` **et** `No results` | Liste vide reconnue | Indicateurs de zéro visibles sur la [surface Baen](https://www.baen.com/allbooks/category/index/id/2012%2C2001), contrat DOM non garanti |
| HTTP 200, aucune carte, pas de double indicateur de zéro | Liste vide avec **`partial_results`**, et non zéro attesté | La structure HTML, un login ou une transition de site ne permettent pas de prouver l'absence |
| Requête non textuelle/contrôle/longueur excessive | `invalid_query`, avant HTTP | Pas de coercition de données privées en recherche |

La page officielle actuelle montre `Showing 1-20 of 82`, des numéros de
pages et un état `No results` dans un autre filtre.
Cela atteste une **surface publique** et des libellés, **pas** que la route
`q` actuelle accepte tous les termes ou filtre le serveur de manière
exhaustive. La consultation ne démontre pas non plus les CGU/robots ou le
comportement du client sur un poste utilisateur.

Tests hors réseau : `test_search_capabilities_p1.py` (champ/scopes, pages,
zéro), `test_search_failures_p1.py` (401/403/429/5xx, panne),
`test_baen_search_evidence_p2.py` (erreur de requête, double marqueur de
zéro, avertissement partiel et droits absents). Le chemin d'authentification
`My Books` n'est jamais emprunté.

**Sortie P2** : ces preuves attestent la déclaration et le comportement
fail-closed du plugin ; elles ne constituent **pas** une recette HTTP live
ou une autorisation d'automatisation de Baen.
