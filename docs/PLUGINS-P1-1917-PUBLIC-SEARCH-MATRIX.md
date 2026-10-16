# Plugins P1 #1917 — matrice probante de la recherche des catalogues publics

Référence : [#1917](https://github.com/pzia/cifereca/issues/1917).
Revue **statique et tests offline** du code sur `main` le 16 octobre 2026.
Les routes, les options HTML et les réponses distantes **n'ont pas été
requalifiées contre les services réels**. La matrice décrit des comportements
du code, non un engagement de couverture exhaustive des catalogues.

## Capacités déclarées et profondeur de recherche

| Provider | Champs annoncés | Exécution réellement observée dans le code | Pagination, scope et limite |
|---|---|---|---|
| BnF Catalogue général (`provider-bnf-catalogue`) | `all/title/author/isbn/subject/series/collection` | Index SRU distincts pour les cinq premiers ; `series/collection` via `bib.anywhere` **puis filtre local** | Page SRU, 50 par défaut ; `physical/digital` filtrent le support **sur la page seulement** |
| Gutenberg (`provider-gutenberg`) | `all/title/author/subject/language` | Une requête texte OPDS, puis post-filtre **local sur la page reçue** pour les champs spécialisés | `next-link`, 25 par page, 101 pages demandables ; `all/digital` |
| Gallica (`provider-gallica`) | `all/title/author/subject/isbn` | `all` EPUB via OPDS/OpenSearch ; index SRU EPUB pour les champs ; branche audio SRU indépendante et optionnelle | Pages de 50 **par branche**, `all/digital` ; aucune recherche physique |
| EbooksGratuits (`ebooksgratuits`) | `all/title/author/subject/category/genre` | `all/title` : formulaire `titre` (**pas** de global multi-attributs) ; `author/category/genre` : option de sélecteur HTML ; `subject` : choix de catégorie **ou** genre | Pages de 50 par défaut ; `all/digital` ; catégories/genres non inventés |
| Baen Free Library (`baen`) | `all` seulement | Formulaire public général ; aucune garantie d'une recherche auteur/ISBN exacte | `page`, 50 par défaut ; `all/digital` |
| Paris (`provider-paris`) | `all` seulement | Recherche textuelle Syracuse, scénarios physiques et numériques indépendants ; **aucun filtre serveur auteur/ISBN attesté** | `page`, 50 par scénario, retry à 20 sur erreur métier ; `all/digital/physical` |
| Kobo (`provider-kobo`) | `all` seulement | Requête Kobo-Vault `field=all` ; `catalog` public uniquement ; `all/digital` peuvent combiner bibliothèque **si compte attesté** | `page`, limite 50 ; scopes `all/digital/catalog` ; aucun champ spécialisé prouvé côté Vault |
| CifericaOPDS (`cifericaopds`) | **Fixture :** `all/title/author/isbn/language` ; **HTTP candidat :** `all` | Fixture bornée filtrée **localement** ; HTTP candidat `q/source_id` seulement, sans filtre serveur par attribut confirmé | `pagination=none`, scope `all` ; en HTTP candidat, page > 0 refusée ; route distante non validée |

## Faux zéro, refus et avertissement partiel

- **`unsupported_field` / `unsupported_scope`** : Baen, Paris, Kobo,
  BnF, Gallica, Gutenberg et EbooksGratuits refusent explicitement les
  champs/périmètres non déclarés, **avant requête**. Le Core peut proposer
  `all` mais ne doit pas l'étiqueter « recherche exacte par attribut ».
- **`field_route_unavailable`** : EbooksGratuits rejette le champ dont le
  sélecteur HTML n'est pas présent. Une option non trouvée sur un sélecteur
  disponible renvoie normalement `[]` ; ce zéro ne décrit que les options
  publiées, pas toutes les fiches du catalogue.
- **`partial_results`** : Gutenberg (post-filtre OPDS), BnF (post-filtre
  SRU/support), Gallica (branche saine + branche défaillante),
  EbooksGratuits (`all` limité au titre, sélecteur sujet manquant),
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
| EbooksGratuits | `src/plugins/ebooksgratuits/cifereca_ebooksgratuits.py` | `tests/test_field_status_p1.py`, `tests/test_public_http_search_errors_p1.py` ; [#1924](https://github.com/pzia/cifereca/pull/1924), [#1949](https://github.com/pzia/cifereca/pull/1949), [#1950](https://github.com/pzia/cifereca/pull/1950), [#1957](https://github.com/pzia/cifereca/pull/1957) |
| Baen | `src/plugins/baen/cifereca_baen.py` | `tests/test_search_capabilities_p1.py` ; [#1923](https://github.com/pzia/cifereca/pull/1923), [#1944](https://github.com/pzia/cifereca/pull/1944) |
| Paris | `src/plugins/provider-paris/cifereca_paris_client.py` et `cifereca_paris.py` | `tests/test_search_capability_contract_p1.py` ; [#1926](https://github.com/pzia/cifereca/pull/1926), [#1953](https://github.com/pzia/cifereca/pull/1953), [#1955](https://github.com/pzia/cifereca/pull/1955) |
| Kobo | `src/plugins/provider-kobo/cifereca_kobo.py` | `tests/test_kobo_search_capabilities_p1.py` ; [#1942](https://github.com/pzia/cifereca/pull/1942) |
| CifericaOPDS | `src/plugins/cifericaopds/cifereca_cifericaopds.py` | `tests/test_cifericaopds.py` ; [#1932](https://github.com/pzia/cifereca/pull/1932) |

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
   ne constituent pas une attestation de service réel.
4. Confirmer l'absence de déduction de possession, de téléchargement
   ou de droits DRM depuis les seules notices publiques.
5. **Numilog #1886 P0 est explicitement hors périmètre de cette matrice** :
   il possède ses propres contrats et sa propre recette, sans rétrogradation.

Cette matrice complète les commentaires de #1917 ; elle ne crée pas de
nouveau contrat Core parallèle.
