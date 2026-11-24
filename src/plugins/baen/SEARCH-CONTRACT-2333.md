# #2333 — Qualification du catalogue public Baen

## Contrat actif

Le plugin reste opt-in. My Books, achats et droits personnels sont hors périmètre (#539).

| Surface | Implémentation | Qualification |
|---|---|---|
| Navigation sans texte | POST /allbooks/category/getBooks, categoryId=2012, page=page+1, pageSize=20, filter=release_date, dir=DESC, hideBundles=0, hideEarcs=0, showAvailable=false | Schéma navigateur attesté page 2 ; HTTPX live non vérifié |
| Recherche libre non vide | GET /allbooks/category/index/id/2012 avec q et pagination historique | Sémantique serveur non attestée ; aucun résultat exact ou exhaustif revendiqué |
| Champs author/title/isbn/subject | unsupported_field avant HTTP | Non pris en charge |
| Scopes all/digital | Catalogue public uniquement | Autres scopes refusés |
| Pages 0..100 | Traduction en pages 1..101 | Bornes client, non preuve de couverture serveur |
| Réponse aboutie | partial_results | Même pour une page vide |
| JSON invalide | catalogue_schema_unverified | Pas de faux zéro |
| HTTP 401/403/429/5xx, panne | Erreurs typées, sans retry de contournement | Pas de faux succès |
| Détail / acquisition | URL documentaire contrôlée ; aucune livraison anonyme attestée | Aucun droit déduit de isFree/isOwned |

## Preuves et limites

Deux captures privées du 11/11/2026 attestent le POST de navigation page 2, HTTP 200 JSON, totalBooks=82, numOfBooks=20 et 20 objets productData gratuits. Le total est propre à la catégorie observée. Le corps de la page 1 et des fiches ouvertes est omis. Le GET /download/index/owned/id/18339 redirige vers le login : aucun téléchargement accompli. Aucune archive privée publiée.

Tentative HTTPX publique sans cookie du 22/11/2026 : résolution DNS EAI_AGAIN avant réponse HTTP. La reproductibilité HTTPX, la couverture page 1/2 et la recherche q restent **unverified** ; recherche exhaustive, champs spécialisés et livraison anonyme ne sont pas des capacités qualifiées. Aucun contournement ni nouvelle capture requis. La navigation JSON est une implémentation prudente du schéma observé, pas une garantie de disponibilité réelle.

## Réception technique

#7344, #7347 et #7489 intégrées avec PR CI SUCCESS sur leurs HEAD exacts. Les gardes historiques de #3050, #3347, #3349 et #3352 restent applicables. Tests hors réseau : validation des champs/scopes, pagination, erreurs HTTP, liens et absence de droits, JSON observé. Le chemin historique GET reste explicitement partiel. Les routes Magento alternatives ne sont pas des replis automatiques.

Sortie proposée selon le critère alternatif du ticket : qualification explicite des capacités non prises en charge ou non attestées, sans prétendre à une recette HTTPX live. Aucune release ni activation par défaut.

## Historique du contrat antérieur

### #2333 P1 — Contrat de recherche Baen Free Library

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
| `page=0..100` | HTTP `page=1..101` ; catégorie JSON observée `pageSize=20` si `query=""`, ancien chemin GET `pageSize=50` demandé si requête non vide ; page invalide `invalid_page` | Taille `page_size` de la déclaration générique `None` car non uniforme. Paramètres transmis, application serveur non qualifiée, aucun crawl |
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

