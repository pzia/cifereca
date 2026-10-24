# #2333 P1 — Contrat de recherche Baen Free Library

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
| `page=0..100` | HTTP `page=1..101`, `pageSize=50` ; page invalide `invalid_page` | Paramètres transmis ; application serveur non qualifiée, pas de crawl de pages voisines |
| Panne réseau / HTTP 5xx | `service_unavailable` | Aucun faux résultat vide |
| HTTP 401 / 403 / 429 | `auth_required` / `access_denied` / `rate_limited` | Pas de contournement ni redirection |
| HTTP 200, au moins une carte publique | Notice sans achat, `owned=false`, `downloadable=false`, **`partial_results`** : requête/pagination non qualifiées | HTML des cartes uniquement ; pas d'acquisition pendant search |
| HTTP 200, aucune carte, `Showing 0-0 of 0` **et** `No results` | Liste vide avec **`partial_results`** : zéro de la page seulement, requête/pagination non qualifiées | Indicateurs de zéro visibles sur la [surface Baen](https://www.baen.com/allbooks/category/index/id/2012%2C2001), contrat DOM non garanti |
| HTTP 200, aucune carte, pas de double indicateur de zéro | Liste vide avec **`partial_results`**, et non zéro attesté | La structure HTML, un login ou une transition de site ne permettent pas de prouver l'absence |
| Requête non textuelle/contrôle/longueur excessive | `invalid_query`, avant HTTP | Pas de coercition de données privées en recherche |

**Écart de route live, connu et hors de cette correction #2333.**
La [Free Library actuellement peuplée](https://www.baen.com/catalog/category/view/s/free-library/id/2012)
affiche `Items 1-12 of 82`, et
[la page `p=2`](https://www.baen.com/catalog/category/view/s/free-library/id/2012/?p=2)
affiche `Items 13-24 of 82`. Le plugin utilise encore l'ancien chemin
`/allbooks/category/index/id/2012` et ses anciens paramètres `q/page/pageSize`,
**non qualifiés comme équivalents** aux routes `catalog/category` et `p`
observées. Le routeur Magento et les sélecteurs de cartes réelles font
l'objet de l'audit **#764** et de la PR brouillon **#1057**, bloquée
sur des fixtures HTML brutes autorisées. Aucune promotion implicite de
`/catalog/category` n'est effectuée dans #2333.

Un ancien écran peut présenter `Showing 0-0 of 0` / `No results` ;
ces mots ne démontrent **ni** la validité de la recherche `q`, **ni**
une absence de livres sur la route peuplée. Le contrat #2333
signale désormais `partial_results` pour **toute réponse aboutie**, positive
ou vide, y compris si ces deux marqueurs sont présents. Le filtrage `q` et
la pagination ne sont pas attestés par un HTML de page vide. Un avertissement
supplémentaire indique un HTML vide non reconnu. Aucun de ces avertissements
ne corrige le routage ni ne crée un repli réseau. Les erreurs HTTP/transport
restent des erreurs typées, sans conserver un avertissement de réponse passée.
La consultation documentaire ne démontre ni les
CGU/robots ni un GET automatisé autorisé depuis l'installation utilisateur.

Tests hors réseau : `test_search_capabilities_p1.py` (champ/scopes, pages,
page vide partielle), `test_search_failures_p1.py` (401/403/429/5xx, panne),
`test_baen_search_evidence_p2.py` (erreur de requête, double marqueur de
zéro, avertissement partiel et droits absents). Le chemin d'authentification
`My Books` n'est jamais emprunté.

**Sortie P1 — version 0.1.12** : ces preuves attestent la déclaration et le comportement
fail-closed du plugin ; elles ne constituent **pas** une recette HTTP live
ou une autorisation d'automatisation de Baen.
