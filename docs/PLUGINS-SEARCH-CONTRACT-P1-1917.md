# Recherche des fournisseurs — matrice de vérité Plugins P1 (#1917)

**Audit du code `main`, 19 octobre 2026.** Ces mentions établissent
les **capacités effectivement annoncées et les routes codées** dans les
plugins ; elles ne constituent **pas** une recette réseau en conditions
réelles. Les branchements Core Search / GTK et les cas refus réseau
doivent être testés séparément. [Ticket P1 #1917](https://github.com/pzia/cifereca/issues/1917).

## Champs et sémantique

| Fournisseur (code) | Champs déclarés `ProviderSearchCapabilities.fields` | Recherche / spécialisation réellement implémentée | Pagination / scopes | Garantie de couverture et alternative |
|---|---|---|---|---|
| **BnF Catalogue général** — [plugin](../src/plugins/provider-bnf-catalogue/cifereca_bnf_catalogue.py) | `all,title,author,isbn,subject,series,collection` | **SRU** `bib.anywhere` (all), `bib.title`, `bib.author`, `bib.isbn`, `bib.subject` ; **series/collection** : SRU général **puis post-filtre local**. | Pages 0–100 (101 pages max), 50/page par défaut ; `all,physical,digital`. | Les supports `physical/digital` sont filtrés **sur les notices reçues** ; les résultats spécialisés localement portent `partial_results`. Aucun téléchargement ni prêt de notice BnF. |
| **Project Gutenberg** — [plugin](../src/plugins/provider-gutenberg/cifereca_gutenberg.py) | `all,title,author,subject,language` | Requête générale **OPDS**, champs spécialisés filtrés **localement sur la page** (préfixe `bookshelf:` accessible par la requête générale). | `next-link`, 101 pages max, `all,digital`. | `partial_results` si filtrage local, même pour 0 correspondance ; ne pas annoncer une interrogation serveur auteur/langue exacte. |
| **Gallica** — [plugin](../src/plugins/provider-gallica/cifereca_gallica.py) | `all,title,author,subject,isbn` | `all` : branche livres **OpenSearch/OPDS** ; champs spécifiques : index **SRU EPUB** ; branche **audio SRU** indépendante/optionnelle. | Page 0–100, 50 par branche, `all,digital`. | En cas d'échec d'une branche, conserver les résultats disponibles **avec alerte de couverture** ; ni catalogue physique ni collection privée. |
| **EbooksGratuits** — [plugin](../src/plugins/ebooksgratuits/cifereca_ebooksgratuits.py) | `all,title,author,subject,category,genre` | Backend par défaut **COPS OPDS Atom** : découverte `rel=search` OpenSearch, puis navigation sous-catalogue attestée ; champs spécialisés **post-filtres locaux**. Ancien HTML **uniquement en option/test** avec formulaire dédié. | Page 0–100, avancée uniquement via les liens `rel=next` vérifiés, `all,digital`. | `partial_results` pour champs spécialisés, branches alternatives ou root non exhaustif. Les liens d'acquisition ouverts peuvent proposer un EPUB, mais `owned=false` ; un téléchargement déjà présent dans un cache local ne prouve pas une recherche distante. |
| **Bibliothèques de Paris** — [plugin](../src/plugins/provider-paris/cifereca_paris.py) | **`all` seulement** | Recherche textuelle Syracuse générale ; les autres champs (auteur/ISBN/sujet) sont **refusés avant réseau** et ne sont pas silencieusement convertis en `all`. | Page 0–100, 50/page, `all,digital,physical`. | Branches physiques et numériques potentiellement distinctes avec avertissement de défaillance partielle ; l'inventaire/PNB et les droits de compte sont des opérations séparées. |
| **Kobo (Vault)** — [plugin](../src/plugins/provider-kobo/cifereca_kobo.py) | **`all` seulement** | Vault recherche générale ; les clés fournisseur supplémentaires **ne prouvent pas** la sémantique serveur d'un filtre `author/isbn`. `catalog` force la projection **publique**, `all/digital` peuvent inclure bibliothèque privée **seulement si identité autorisée**. | Page 0–100, 50/page, `all,digital,catalog`. | En cas de refus du privé 401/403, catalogue public possible **avec avertissement `partial_results`**. Aucune acquisition ou statut acheté inféré du repli. |
| **Numilog public, référence P0 #1886** — [plugin](../src/plugins/provider-numilog/cifereca_numilog_public_provider.py) | **`all` seulement** | Recherche publique anonyme `brSimple` **sans champ serveur spécialisé qualifié** ; post-filtres sur pages/produits explicitement chargés sous preuve de sélecteur HTML / JSON-LD. | `GRID_PAGE` borné 0–100, pagination/facettes **par liens attestés seulement** ; `all,digital`. | Source distincte de **Mes livres** privé. Une offre publique n'autorise pas EPUB/ACSM ni format acheté. Le formulaire public « Recherche avancée » et ses paramètres HTTP nécessitent qualification live [#1886](https://github.com/pzia/cifereca/issues/1886). |

**Deux axes indépendants** : (a) `catalog` public / recherche anonyme
ou route publique interne, et (b) **preuve privée de possession**. Le
CoreUI peut regrouper visuellement une enseigne sans fusionner
`source_provider`, `source_credential`, `owned` ou les URLs de
livraison. Une notice OPDS avec acquisition libre reste **non possédée**
(`downloadable=True` peut être correct, `owned=False` demeure vrai).

## États d'erreur et portée à préserver

| Signal | Sens observable | Règle d'affichage / fallback |
|---|---|---|
| `unsupported_field`, `unsupported_scope` | Filtre/portée non proposé(e). | **Pas un zéro de catalogue** ; proposer explicitement `field=all` si l'utilisateur l'accepte, jamais prétendre au même filtre. |
| `field_route_unavailable`, `search_route_unavailable`, `browse_route_unavailable` | Route/branche de spécialisation non qualifiée ou absente. | Ne pas fabriquer une requête serveur. Un repli indépendant peut être proposé avec son scope affiché. |
| `auth_required`, `access_denied` (401/403) | Refus de la route privée ou du service. | Pas d'escalade vers une session authentifiée différente ; le catalogue public indépendant peut subsister sans droits privés. |
| `rate_limited` (429), `service_unavailable` / timeout | Service indisponible ou budget atteint. | Ne jamais remplacer par `[]` silencieux ; conserver la cause typée, pas le corps brut de réponse. |
| `partial_results` | Une branche ou page reçue seulement, filtre local, plusieurs routes possibles. | **Avertir même si le tableau final est vide.** Un 0 local ne prouve pas 0 global. |
| `[]` sans avertissement | Réponse vide d'une recherche réellement autorisée et effectuée dans sa portée. | Réserver le « Aucun résultat » au périmètre demandé vérifiable ; ne pas extrapoler à d'autres pages, branches ou comptes. |

L'ancien HTML d'EbooksGratuits ne se substitue **jamais** automatiquement
à l'OPDS si celui-ci échoue ; les chemins `/account`, signed URLs ou
hôtes étrangers ne sont pas des routes publiques de recherche de secours.

## Recette minimale reproductible — à faire avant clôture

1. **Recherche hors cache et appel attesté** : choisir un terme/sujet
   inédit, mesurer la présence de `ProviderSession.search()` puis des
   requêtes réellement déclenchées par la route du fournisseur (avec
   cookies, identifiants, jetons et URLs signées **expurgés**). Distinguer
   « session connectée mais jamais interrogée », « GET tenté et refusé »,
   « GET répondu sans correspondance » et « notice nouvelle retournée ».
2. **Par fournisseur** : vérifier `field=all`, une spécialité déclarée,
   un champ non supporté, page initiale et page suivante, zéro réel,
   panne 401/403/429/5xx, gestion `partial_results` y compris avec
   zéro ligne, et absence de bascule implicite compte privé.
3. **Flux EbooksGratuits** : l'éditeur atteste
   [`https://opds2.ebooksgratuits.org/index.php/feed`](https://www.ebooksgratuits.com/torrent.php)
   comme nouveau catalogue Calibre/COPS. Vérifier en direct, de façon
   autorisée et expurgée, la racine Atom, `rel=search`,
   OpenSearchDescription, navigation et `rel=next` ; enregistrer un
   échantillon XML **réel**. Les tests existants et les PR Plugins
   [#2442](https://github.com/pzia/cifereca/pull/2442) / [#2444](https://github.com/pzia/cifereca/pull/2444) /
   [#2445](https://github.com/pzia/cifereca/pull/2445) utilisent des
   fixtures synthétiques et **ne prouvent pas** la disponibilité actuelle.
4. **Core/Search et GTK** : vérifier que la source a réellement été
   interrogée, que les nouveaux livres apparaissent, que les erreurs
   et avertissements restent affichés et que les comptes privés ne
   sont ni consultés ni modifiés pendant une recherche publique. Voir
   [#2361](https://github.com/pzia/cifereca/issues/2361) (EbooksGratuits)
   et [#1867](https://github.com/pzia/cifereca/issues/1867) (UX de
   regroupement par enseigne).

## État

- **Couvert par audit de code** : champs, scopes et stratégies de
  pagination listés ci-dessus ; références au code source liées.
- **Non attesté par ce document** : disponibilité réelle des 6 API,
  flux OPDS live EbooksGratuits, exactitude des résultats multi-pages
  en production, affichage fédéré Core/UI et droits d'utilisateur.
- **#1917 demeure P1 ouvert** pour les recettes end-to-end et le
  classement d'erreurs par fournisseur. **#1886 reste P0 Numilog**.
- Les périmètres Baen et CifericaOPDS sont traités séparément aux
  tickets [#2333](https://github.com/pzia/cifereca/issues/2333)
  et [#969](https://github.com/pzia/cifereca/issues/969).
