# Numilog — fixture publique de recherche Jefferson et fiche produit

Qualification P0 #1886, rôle Plugins, le 16 octobre 2026. Cet incrément documente les preuves et fournit des fixtures hors réseau ; il ne branche pas encore le catalogue public dans le provider.

## Sources et niveau de preuve

- Recherche demandée : <https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson>.
- Source HTML de recherche : fichier `ResultatRecherche.aspx` fourni dans la conversation, 272 024 octets. Sa date de capture est inconnue. La date du nom de fixture est celle de qualification, pas un horodatage de prix.
- L'ouverture de la recherche par l'outil web n'a pas fourni de contenu. Aucun succès HTTP direct et anonyme de cet endpoint n'est donc attesté.
- Fiche du premier résultat : <https://www.numilog.com/1958476/jefferson-prend-des-risques.ebook>, texte public récupéré par l'outil web le 16 octobre 2026. Le DOM brut et les en-têtes HTTP n'ont pas été obtenus. Cette preuve est distincte du HTML de recherche fourni ; elle ne prouve pas le futur transport HTTP du provider.
- Le HTML fourni comprend une coque de compte et des états ASP.NET. Seuls les fragments bibliographiques publics assainis sont versionnés. Le fichier original, les valeurs de compte, champs cachés et liens privés ne sont pas conservés dans le dépôt.

## Structure de la recherche observée

| Champ | Sélecteur / source | Présence et traitement |
| --- | --- | --- |
| Grille | `table#ctl02_CentrePage_ListeLivres1_GridView1` | Une grille |
| Résultat | `tr.LivreDuThemeDetail`, `div.BestSalesItem` | 20 cartes, 20 IDs produit distincts |
| Total | `.ResultatDetail` | Libellés `242` et `résultat(s)` ; total annoncé, pas 242 fiches collectées |
| Titre / URL | `a.TitleBook`, texte, `title`, `href` | 20 ; conserver le titre complet |
| Auteurs | `a.Author` | 28 liens sur 20 cartes ; rôles non précisés |
| Éditeur | `.product_meta .LabelInfos`, préfixe `Éditeur :` | 20 |
| Collection | même zone, préfixe `Collection :` | 13 valeurs présentes ; absence = null |
| Parution | même zone, préfixe `Parution :` | 20 dates ISO affichées |
| Formats | même zone, préfixe `Format(s) :` | Libellés conservés |
| Extrait | `p.list-group-item-text` | 20 ; `summary_kind=listing_excerpt`, pas une description intégrale garantie |
| Couverture | `img.lazy-image[data-src]` | 20, origine `https://couverture.numilog.com`, `src` vide |
| Format choisi | `.FormatLivre` | 9 « Choix format », donc aucun format sélectionné ; 11 libellés uniques |
| Options | `.AvailableFormat` | 9 cartes à deux options PDF/ePub ; attributs bruts `formatid=3/14`, `availablelcp=1` |

Les URLs livre sont relatives (`../../ID/slug.ebook`) : les résoudre contre l'URL source, puis vérifier origine HTTPS exacte et chemin public. Le validateur privé qui refuse les segments `..` ne peut pas être réutilisé directement avant résolution. Les auteurs, éditeurs et collections ont également des liens publics conservés dans le HTML assaini.

Ne pas interpréter l'ID produit ou le nom de fichier de couverture comme ISBN. Deux cartes de même titre PDF/ePub peuvent avoir des IDs distincts ; aucune fusion sur le titre ou la couverture. `availablelcp` reste un attribut observé, sans déduction de droits ou de prise en charge d'acquisition. Seules quatre cartes portent explicitement « epub sans DRM ». Aucun JSON-LD n'a été trouvé.

La recherche ne fournit pas d'ISBN numérique/papier, langue par carte, rôles d'auteur, tranche d'âge ou numéro de tome structuré. Ces champs restent null. Les fragments « Tome 1/2 » restent dans le titre. Les facettes de langue ne déterminent pas la langue d'une carte.

## Prix : métadonnée obligatoire de la fixture

Chaque carte possède `.ColPrice .LblPrice` (euros entiers), `.ColPrice .LblDecimal` (deux chiffres), `.ColPrice .LblSeparatorPrice` (symbole €). La virgule est du texte entre les spans : extraire seulement le premier span perd les centimes.

Exemple du premier résultat :

```json
{"amount":"9.99","minor_units":999,"currency":"EUR","display_reconstructed":"9,99 €","raw_parts":["9","99","€"],"applies_to":"displayed_card_offer","tax_included":null}
```

`amount` est une chaîne décimale, jamais un flottant. Les trois sélecteurs source sont enregistrés dans `price.source_selectors`. La provenance est `price_observation_kind=user_supplied_snapshot`, `price_capture_timestamp=null`, `metadata.source_url` égale l'URL de recherche. Un prix de carte ne devient pas un tableau de prix par format. Aucun ancien prix barré, promotion, TVA/TTC ou statut de stock exploitable n'est attesté. Le bouton commercial ne prouve ni possession ni téléchargement autorisé. Les valeurs sont une observation de capture, pas une garantie de prix courant.

| Rang | ID produit | Titre | Formats affichés | Prix carte |
| --- | --- | --- | --- | --- |
| 1 | 1958476 | Jefferson prend des risques | PDF, ePub | 9,99 € |
| 2 | 1890322 | Jefferson (Tome 2) - Jefferson fait de son mieux | PDF | 13,99 € |
| 3 | 1864213 | Jefferson (Tome 2) - Jefferson fait de son mieux | ePub | 13,99 € |
| 4 | 1875232 | Thomas Jefferson, la Nature, l'individu et la liberté | PDF | 23,99 € |
| 5 | 1293183 | Jefferson | PDF, ePub | 6,49 € |
| 6 | 771088 | Jefferson | ePub | 16,99 € |
| 7 | 1683489 | Jefferson se fâche | PDF, ePub | 7,49 € |
| 8 | 140636 | Le Code Jefferson | epub sans DRM | 13,99 € |
| 9 | 1007533 | Jefferson Blythe | epub sans DRM | 6,99 € |
| 10 | 1708575 | Thomas Jefferson | epub sans DRM | 15,99 € |
| 11 | 1659403 | Thomas Jefferson | PDF, ePub | 40,41 € |
| 12 | 1620010 | Thomas Jefferson | PDF, ePub | 17,27 € |
| 13 | 1766583 | Thomas Jefferson | PDF, ePub | 30,78 € |
| 14 | 1730842 | Jefferson (Tome 1) | PDF | 14,99 € |
| 15 | 1890327 | Jefferson (Tome 1) | ePub | 14,99 € |
| 16 | 658364 | The Jefferson Key | ePub | 3,99 € |
| 17 | 1629169 | Jefferson Davis's Generals | PDF, ePub | 12,33 € |
| 18 | 1628842 | Adams vs. Jefferson | PDF, ePub | 17,27 € |
| 19 | 1836757 | Jefferson-Madison : Un débat sur la République | epub sans DRM | 9,99 € |
| 20 | 1420696 | Jefferson fait de son mieux | PDF, ePub | 7,49 € |

## Navigation et contrôles : preuves et limites

- Pagination : `tr.pagination`, `.PagerCurrentPage` vaut `1`. Liens GET « 2 » → `GRID_PAGE=1`, « 3 » → `GRID_PAGE=2`, « > » → `GRID_PAGE=1`, en conservant `mode=brSimple&query=jefferson`. Ces liens suggèrent un index à zéro ; aucune réponse de page 2/3 n'a été capturée. Ne pas inventer un postback de pagination ou prétendre avoir parcouru 242 résultats.
- 42 liens `.panel-sous-filtre` : 20 `fltrTheme`, 9 `fltrPrice` (0, 5, 10, 15, 20, 25, 30, 35, 40), 3 `fltrLang` (`fr`, `english`, `es`) et 10 `fltrPub` visibles. Le JSON conserve chaque libellé, valeur et URL. Les compteurs de langue sont 46/195/1 ; les compteurs prix 114/18/15/9/7/7/6/9/57. Leurs sommes sont 242, sans preuve de la sémantique exacte des tranches de prix. Ne pas convertir `english` en `en` dans les requêtes sortantes.
- Le contrôle de tri `select#ListeTrier`, nommé `ctl02$CentrePage$ListeLivres1$ListeTrier`, propose `RANK`, `Date Parution desc`, `Price asc`, `TITRE asc`, `AUTEUR asc`. Son transport n'est pas qualifié : aucun paramètre GET de tri inventé.
- Le « voir plus » des éditeurs utilise un postback non qualifié, absent de la fixture assainie. Les réponses filtrées, combinaisons de filtres et autres modes de recherche ne sont pas attestées. Seul `brSimple` est observé ; aucun mode auteur/ISBN spécifique démontré.

## Fiche produit du résultat 1958476

La fixture sémantique `public_product_1958476_20261016.observed.json` conserve les champs publics lus et leur URL source. Elle n'est pas une fixture HTML : aucun sélecteur DOM produit n'est inventé. Le titre, auteur, éditeur, collection et date concordent avec la première carte. La page ajoute un nombre de pages, une langue, un EAN papier et deux EAN numériques distincts, ainsi que des limites de copie/impression et tailles brutes par format.

| Champ de la fiche produit | Valeur observée |
| --- | --- |
| Pages / langue | 288 / Français |
| EAN papier | `9782075241939` |
| PDF | EAN `9782075241946` ; 9,99 EUR ; 19 231 Ko |
| EPUB | EAN `9782075241953` ; 9,99 EUR ; 37 980 Ko |
| Copie / impression | 0 page / 0 page pour chaque format |
| Protections affichées sur la page | DRM LCP ; DRM Adobe |

Les prix numériques sont rangés sous chaque format et portent `scope=format_offer` : cette attribution est démontrée sur la fiche produit, contrairement au prix global de la carte. Ne pas confondre l'EAN papier et l'EAN numérique de chaque édition. Les labels DRM sont observés au niveau de la page, sans attribution inventée par format. L'unité `Ko` reste brute ; aucune conversion en octets n'est présumée. L'extrait de lecture, les achats et les téléchargements n'ont pas été suivis. Les métadonnées de cette fiche ne s'appliquent pas automatiquement aux 19 autres résultats.

## Contrat, assainissement et tests

Fichiers sous `src/plugins/provider-numilog/tests/` :

- `fixtures/public_search_jefferson_20261016.html` : fragments réels de grille, facettes, total, tri et pagination, extraits de la capture. Ce n'est pas une page interactive complète.
- `fixtures/public_search_jefferson_20261016.expected.json` : schéma `numilog.public_search.fixture/1`, pagination, facettes, tri et métadonnées des 20 cartes.
- `fixtures/public_product_1958476_20261016.observed.json` : observation structurée du texte produit, avec niveau de preuve explicite.
- `public_search_fixture_contract.py` : lecteur de qualification hors réseau, réservé aux tests ; aucune intégration de recherche du provider n'est revendiquée.
- `test_numilog_public_search_fixture_p0.py` : comparaison intégrale, 20 prix exacts, identités PDF/EPUB, champs inconnus, non-possession, pagination, formats, absence de coque privée et rejets de prix incomplets/devise non qualifiée.

Assainissement : exclusion des scripts, styles, iframes, formulaires, inputs, état ASP.NET, trackers, compte, panier, envies et lecteurs. Suppression des attributs événementiels, IDs d'action et données non bibliographiques ; seuls les liens publics attendus et les couvertures sans query sont conservés. Les textes bibliographiques, liens publics et parties de prix sont préservés. Les valeurs longues d'état et de compte originales sont comparées hors ligne aux livrables pour vérifier leur absence, sans les journaliser.

Toutes les cartes/observations portent `source_collection=public_catalog`, `owned=false`, `downloadable=false`, `download_url=null`. Ce contrat interdit de transformer un résultat commercial en livre acquis. Aucune requête d'acquisition ni achat n'est nécessaire pour ces tests.

Commande ciblée : `PYTHONPATH=src python -m pytest -q src/plugins/provider-numilog/tests/test_numilog_public_search_fixture_p0.py` (dans l'environnement projet).

## Suite de #1886

Le ticket reste ouvert : implémenter le client public sans cookies privés, l'intégration du catalogue et des métadonnées dans le provider, les limites/cache et le détail à la demande sans N+1. Qualifier le DOM produit, l'accès HTTP anonyme réel, les conditions d'accès applicables, les réponses de page suivante, zéro résultat, erreurs/403/429 et modes multi-attributs attestés. Ajouter ensuite les tests du transport et de l'intégration réelle. Cette documentation ne prétend pas résoudre ces critères ni changer le parcours privé Mes achats.
