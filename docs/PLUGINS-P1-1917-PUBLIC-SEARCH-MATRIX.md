# Plugins P1 #1917 — matrice probante de la recherche des catalogues publics

Référence : [#1917](https://github.com/pzia/cifereca/issues/1917).
Revue **statique et tests offline** du code sur `main` le 16 octobre 2026 ; mise à jour du périmètre et du backend EbooksGratuits par défaut le **19 octobre 2026**.
Les routes, les options HTML et les réponses distantes **n'ont pas été
requalifiées contre les services réels**. La matrice décrit des comportements
du code, non un engagement de couverture exhaustive des catalogues.

## Capacités déclarées et profondeur de recherche

| Provider | Champs annoncés | Exécution réellement observée dans le code | Pagination, scope et limite |
|---|---|---|---|
| BnF Catalogue général (`provider-bnf-catalogue`) | `all/title/author/isbn/subject/series/collection` | Index SRU distincts pour les cinq premiers ; `series/collection` via `bib.anywhere` **puis filtre local** | Page SRU, 50 par défaut ; `physical/digital` filtrent le support **sur la page seulement** |
| Gutenberg (`provider-gutenberg`) | `all/title/author/subject/language` | Une requête texte OPDS, puis post-filtre **local sur la page reçue** pour les champs spécialisés | `next-link`, 25 par page, 101 pages demandables ; `all/digital` |
| Gallica (`provider-gallica`) | `all/title/author/subject/isbn` | `all` EPUB via OPDS/OpenSearch ; index SRU EPUB pour les champs ; branche audio SRU indépendante et optionnelle | Pages de 50 **par branche**, `all/digital` ; aucune recherche physique |
| EbooksGratuits (`ebooksgratuits`) | `all/title/author/subject/category/genre` | **Par défaut :** recherche OPDS Atom/COPS via OpenSearch puis navigation bornée vers une branche livres ; `title/author/subject/category/genre` sont des **post-filtres locaux de la page OPDS** (pas de recherche distante exacte par attribut). Le backend HTML est uniquement une option de compatibilité explicite, pas le comportement par défaut. | Pages bornées par liens `rel=next` ; 50 résultats au plus par page demandée ; `all/digital` ; une branche COPS sélectionnée ne représente pas l'union du catalogue. |
| Baen Free Library (`baen`, **P1 distinct #2333**) | `all` seulement | Recherche générale uniquement ; les preuves DOM et la récupération du catalogue sont suivies hors P1. | `page`, 50 par défaut ; `all/digital` ; **ne bloque pas #1917**. |
| Paris (`provider-paris`) | `all` seulement | Recherche textuelle Syracuse, scénarios physiques et numériques indépendants ; **aucun filtre serveur auteur/ISBN attesté** | `page`, 50 par scénario, retry à 20 sur erreur métier ; `all/digital/physical` |
| Kobo (`provider-kobo`) | `all` seulement | Requête Kobo-Vault `field=all` ; `catalog` public uniquement ; `all/digital` peuvent combiner bibliothèque **si compte attesté** | `page`, limite 50 ; scopes `all/digital/catalog` ; aucun champ spécialisé prouvé côté Vault |
| CifericaOPDS (`cifericaopds`, **P2 #969**) | **Fixture :** `all/title/author/isbn/language` ; **HTTP candidat :** `all` | Fixture bornée filtrée localement ; HTTP candidat sans filtre distant par attribut prouvé. Ne pas transposer les capacités de la fixture au serveur. | `pagination=none`, scope `all` ; route distante non validée ; **ne bloque pas #1917**. |

## Faux zéro, refus et avertissement partiel

- **`unsupported_field` / `unsupported_scope`** : Paris, Kobo,
  BnF, Gallica, Gutenberg et EbooksGratuits refusent explicitement les
  champs/périmètres non déclarés, **avant requête**. Baen suit le même
  contrat dans son chantier P1 séparé #2333. Le Core peut proposer
  `all` mais ne doit pas l'étiqueter « recherche exacte par attribut ».
- **`search_route_unavailable` / `browse_route_unavailable`** : dans le **backend OPDS par défaut** d'EbooksGratuits, OpenSearch absent ou navigation COPS non qualifiée donnent un refus explicite, pas un faux zéro. **`field_route_unavailable`** et les sélecteurs absents concernent seulement l'ancien backend HTML sélectionné explicitement (`acquisition_backend=html`) ; un nom non présent dans ses options ne prouve pas un zéro sur l'ensemble du catalogue.
- **`partial_results`** : Gutenberg (post-filtre OPDS), BnF (post-filtre
  SRU/support), Gallica (branche saine + branche défaillante),
  EbooksGratuits (**backend OPDS par défaut : post-filtre du champ sur la page et/ou choix d'une seule branche de navigation COPS** ; l'avertissement `all` limité au titre ne concerne que l'option HTML),
  Kobo (refus privé 401/403 avec repli public) et Paris (avertissement de
  panne d'une branche) rendent l'incomplétude observable. Les avertissements
  sont remis à zéro à chaque recherche lorsque le provider le prévoit.
- **`auth_required`, `access_denied`, `rate_limited`,
  `service_unavailable`** : erreurs HTTP publiques stables et expurgées
  implémentées notamment par Baen, Gutenberg, BnF et EbooksGratuits ;
  les contrats Paris/Syracuse et CifericaOPDS conservent leurs erreurs
  spécifiques. Ne pas déduire d'un `[]` qu'un HTTP 403/429 a réussi.
- **Zéro valable** : réponse obtenue d'un périmètre effectivement supporté,
  sans panne ni perte de branche. Pour Gallica, une branche saine vide et
  une autre en panne doit signaler `partial_results`, non une indisponibilité
  totale ni une certitude d'absence.

## Correspondances code, tests et intégration

| Source | Code | Tests ciblés et PR de référence |
|---|---|---|
| BnF | `src/plugins/provider-bnf-catalogue/cifereca_bnf_catalogue.py` | [#1928](https://github.com/pzia/cifereca/pull/1928), [#1948](https://github.com/pzia/cifereca/pull/1948) |
| Gutenberg | `src/plugins/provider-gutenberg/cifereca_gutenberg.py` | `tests/test_search_capabilities_475.py`, `tests/test_search_diagnostics_p1.py` ; [#1943](https://github.com/pzia/cifereca/pull/1943) |
| Gallica | `src/plugins/provider-gallica/cifereca_gallica.py` | `tests/test_search_capabilities_p1.py` ; [#1929](https://github.com/pzia/cifereca/pull/1929), [#1946](https://github.com/pzia/cifereca/pull/1946), [#1951](https://github.com/pzia/cifereca/pull/1951) |
| EbooksGratuits | `src/plugins/ebooksgratuits/cifereca_ebooksgratuits.py` | `tests/test_opds_calibre_1084.py`, `tests/test_opds_query_validation_p1.py`, `tests/test_opds_genre_scope_p1.py`, `tests/test_opds_invalid_next_link_p1.py` ; tests HTML historiques `test_field_status_p1.py`, `test_public_http_search_errors_p1.py` ; navigation COPS sécurisée en [PR #2394](https://github.com/pzia/cifereca/pull/2394), non présumée fusionnée |
| Baen (**P1 distinct**) | `src/plugins/baen/cifereca_baen.py` | [#2333](https://github.com/pzia/cifereca/issues/2333) et simulation [#2332](https://github.com/pzia/cifereca/issues/2332) ; hors sortie P1 |
| Paris | `src/plugins/provider-paris/cifereca_paris_client.py` et `cifereca_paris.py` | `tests/test_search_capability_contract_p1.py` ; [#1926](https://github.com/pzia/cifereca/pull/1926), [#1955](https://github.com/pzia/cifereca/pull/1955) (recherche principale), [#1960](https://github.com/pzia/cifereca/pull/1960) (couvertures d’historique ; CI SUCCESS sur `d1420a94`) |
| Kobo | `src/plugins/provider-kobo/cifereca_kobo.py` | `tests/test_kobo_search_capabilities_p1.py` ; [#1942](https://github.com/pzia/cifereca/pull/1942) |
| CifericaOPDS (**P1 distinct**) | `src/plugins/cifericaopds/cifereca_cifericaopds.py` | [#969](https://github.com/pzia/cifereca/issues/969) ; hors sortie P1 |

Ces chemins de tests sont **relatifs au dossier de chaque plugin**.
Les PR ouvertes ne prouvent **pas** l'intégration ni le résultat de la CI sur
le futur HEAD fusionné.

## Gates non levées — pas de clôture automatique #1917

1. Vérifier la CI sur le SHA **fusionné** et la compatibilité Core
   Search #1915/#1916 : refus de champ, repli global **étiqueté**, cache
   et affichage stable de tous les avertissements.
2. Exécuter les scénarios GTK 1366 et 1600 : `all`, auteur non supporté,
   zéro véritable, résultats partiels, 401/403/429/503, changement de profil,
   source désactivée, recherche fédérée multibranches et navigation.
3. Vérifier la réalité des schémas/paramètres des fournisseurs distants
   autorisés en environnement de recette ; les tests synthétiques
   ne constituent pas une attestation de service réel. Pour EbooksGratuits,
   contrôler **le flux OPDS/COPS par défaut**, la branche de navigation et
   `rel=next` ; les sélecteurs HTML anciens ne constituent pas cette preuve.
4. Confirmer l'absence de déduction de possession, de téléchargement
   ou de droits DRM depuis les seules notices publiques.
5. **Numilog #1886 P1 est explicitement hors périmètre de cette matrice** :
   il possède ses propres contrats et sa propre recette, sans rétrogradation.
6. **Baen #2333 est maintenant P1 distinct ; CifericaOPDS #969 reste P2**, sans en faire des
   prérequis de sortie P1, compte tenu des arbitrages ultérieurs du 23 octobre 2026.

Cette matrice complète les commentaires de #1917 ; elle ne crée pas de
nouveau contrat Core parallèle.


## Qualification des erreurs de recherche — 23 octobre 2026

Cette mise à jour est une **preuve de code et de CI synthétique**, pas une
validation des services distants. Les codes Core ci-dessous sont reconnus
par `search_failure_code` et ne proviennent jamais d'un message libre
du fournisseur. Le repli `known_catalog_stale` demeure **public,
identité/source-borné, sans acquisition ni droits privés**.

| Fournisseur | 401 / 403 | 408 / 429 / 5xx / transport | HTTP 200 non qualifié | Preuves et limites |
|---|---|---|---|---|
| **BnF Catalogue** | Refus fermé, pas de secours local. | Indisponibilité fermée, requête SRU unique. | XML invalide/non SRU ou `numberOfRecords` absent = panne, **pas zéro** ; diagnostic SRU métier = refus non repliable. | [#3026](https://github.com/pzia/cifereca/pull/3026), fusion `b65280c`, CI synthétique PASS. **Hors BnF-Prêt**. |
| **Gutenberg** | Refus fermé. | Transport/HTTP indisponible fermé. | XML invalide ou XML valide non Atom = panne ; un vrai `Atom feed` vide reste zéro ; `rel=next` suspect interdit. | [#3019](https://github.com/pzia/cifereca/pull/3019), fusion `af3c94e`, CI PASS. |
| **Gallica** | Quand toutes les branches échouent, le refus l'emporte sur le timeout. | Panne fermée si aucune branche n'a réussi. | Branche saine avec zéro et autre branche en échec = `partial_results`, pas zéro global. | [#3021](https://github.com/pzia/cifereca/pull/3021), fusion `dd6e204`, CI PASS. |
| **EbooksGratuits** | Refus fermé pour HTML optionnel et OPDS par défaut. | Indisponibilité fermée ; aucune relance identique. | Pagination/navigation non qualifiée et sélecteurs HTML absents restent des limites, pas un succès zéro. | [#3022](https://github.com/pzia/cifereca/pull/3022), fusion `a4636cc`, CI PASS. |
| **Paris Syracuse** | Réponse HTTP de refus distincte de l'erreur métier de taille de recherche. | Échec HTTP non réparé par un second POST de taille 20. | La récupération métier 50 → 20 reste autorisée uniquement sur erreur Syracuse non HTTP typée. | [#3028](https://github.com/pzia/cifereca/pull/3028), fusion `f92f545`, CI PASS. Ne concerne pas PNB. |
| **Kobo-Vault public** | Catalogue explicitement public sans droit ; refus privé 401/403 peut conserver sa recherche publique avec `partial_results`. | Un échec public/privé de transport ou 429/5xx n'est pas un succès partiel. | Le schéma de réponse et la portée des droits restent validés côté fournisseur, aucun `owned` public. | [#3029](https://github.com/pzia/cifereca/pull/3029), fusion `b61c657`, CI PASS ; un `downloadable=None` public n’atteste aucun droit. |

Les codes de panne n'impliquent **aucun** retry automatique. Un HTTP 400/404
sémantique, un `rel=next` non sûr ou un refus de droits ne doit pas
activer le secours local. Le classement n'affirme pas que `Retry-After` a
été pris en compte ni qu'un endpoint de recherche réellement indépendant
existe : ces points restent dans [#1916](https://github.com/pzia/cifereca/issues/1916).

Les **preuves manquantes** restent la recette live autorisée et expurgée
(provider et champ hors cache), le succès/zéro/pagination sur les services
réels, les avertissements Core/GTK selon identité et source activée, puis
les gates sur le HEAD de livraison. Le ticket #1917 ne peut pas être clôturé
sur les tests MockTransport seuls. BnF-Prêt, Numilog privé/P0 et opérations
DRM ne sont pas dans ce périmètre.

## Livraisons P1 documentées — 24 octobre 2026

Ces tranches sont des preuves de CI et de tests synthétiques, pas des preuves
de comportement d'un serveur fournisseur réellement interrogé.

| Périmètre | Livraison et CI sur HEAD fusionné | Qualification restante |
| --- | --- | --- |
| Gutenberg : borne OPDS | PR #3059, squash 7ad902c, Plugins CI 40076390602 SUCCESS | Corps HTTPX brut limité à 2 Mio, compression inattendue refusée ; valider auprès du fournisseur réel. |
| EbooksGratuits : XML Atom | PR #3041, squash 49d12a3, Plugins CI 40019646560 SUCCESS | XML 200 malformé ou non-Atom = panne, pas zéro ; DTD refusé reste un refus de sécurité. |
| EbooksGratuits : liens Web | PR #3039, squash 4c73e9c, Core CI 40018359337 SUCCESS | Anciennes URL OPDS XML et alias encodés non proposés comme fiches HTML, EPUB inchangé ; recette #2460 ouverte. |
| Core : graphes d'erreurs | PR #3056, squash 40fa92a, Core CI 40075578824 et install 40075578832 SUCCESS | HTTP 401/403 dans cause OU contexte prime tout timeout ; pas de secours SQLite stale sur refus. |
| Core : boucle redirect | PR #3040, squash a4161c1, Core CI 40018860739 SUCCESS | Boucle HTTP redirection = arrêt de variantes, sans inventer une panne autorisant le secours stale. |
| Baen (P1 distinct) | PR #3053, squash d02d981, simulation CI 40074665018 SUCCESS | Fixtures 401/403/429/503 et timeout ; recherche 200 toujours partielle, car q/page/pageSize non attestés sur Baen réel. |

Les tickets Baen #2333 et simulation #2332 ont été reclassés P1, mais
ne sont toujours pas des prérequis de clôture de #1917.
Numilog public commercial #1886 est P1 séparé, sans preuve de méthode de
recherche avancée complète et sans assimilation aux achats possédés.

**Reste ouvert** : validation live autorisée et datée par fournisseur,
pagination/zéro/branche réellement attestés, tests GTK 1366/1600, identités,
cache et droits distincts. Aucun ticket clos sur ces seules fixtures.
