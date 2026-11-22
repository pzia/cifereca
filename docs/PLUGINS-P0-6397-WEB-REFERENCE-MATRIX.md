# #6397 — Matrice de continuité des fiches web (22/11/2026)

Audit statique des chemins producteurs courants, distinct de la recette GTK
et des captures réseau. Un lien de ligne/détail ne prouve pas son admission
dans C. Pour C public, le Core exige le témoin same-call anonyme, le
producteur de la preuve qualifié et les coordonnées exactes ; les options
relisent la contribution et sa fence à l'ouverture. Aucune URL signée,
session, acquisition, ni équivalence d'édition déduite.

| Provider / source | Champ et parcours actuel | Identité et portée | C / ouverture / preuve restante |
|---|---|---|---|
| Numilog catalogue public | Carte produit `friendly_url` et preuve `public_search_product_card`, fiche JSON-LD `public_product_jsonld` ; HTML observé et parseur public. | Produit exact, session anonyme, collection public_catalog. | Enveloppe same-call et C public ; choix de lien, éviction, restart. Tests `test_p0_6366_federated_public_notice_c.py` et recette mono-lien ; multi-options GTK #7037 fusionnée, CI/E2E vertes sur le HEAD exact (voir reçus ci-dessous). Formulaire avancé #1886 distinct. |
| Numilog Mes livres (auth) | `public_url` de carte bibliothèque (preuve `library_title_link`), redirect Readzis qualifié séparément ; détail explicite. | lid/compte/credential exact ; lien public stable éventuel, pas droit du navigateur. | Enrichissement d'achat #1072 distinct ; la projection publique de la session privée retire URL. Pas de locator C authentifié générique reçu. |
| BnF Catalogue général (public) | SRU DC identifiant ARK → `catalogue.bnf.fr/{ark}`, `public_search_record_identifier`. | ARK de la notice, session __public__ canonique, pas prêt. | Enveloppe same-call, même absence de couverture ; `test_public_c_sru_envelope.py`. Qualification live titre/pagination #6938 ; lien individuel non certifié par un simple GET SRU. |
| Paris (catalogue via session) | `FriendlyUrl` dans `Results[]` de `POST Search.svc/Search`. Les réponses historiques attestent les scénarios `CATALOGUE` et `EBOOKS`. [Relevés #3415](https://github.com/pzia/cifereca/issues/3415#issuecomment-6587935035) | RscBase, RscId, scénario et credential. Le scénario ne détermine pas à lui seul le support. | La déclaration publique retire `friendly_url`. La fixture de parsing ne qualifie ni sa destination par édition ni le mode d'accès. [Test du parseur](../src/plugins/provider-paris/tests/test_p1_3415_search_response_facets.py) · [Frontière inter-profils](../tests/test_core_p0_4673_paris_foreign_search.py) |
| Kobo Vault, catalogue public | `/api/v2/catalog/search` fournit `product_url`, validé par `_public_product_url`. | ID de produit et révision. `scope=catalog` exclut la bibliothèque de compte. | URL en résultat lorsque qualifiée. La déclaration publique retire `friendly_url`. [Fixtures URL produit](../src/plugins/provider-kobo/tests/test_kobo_public_product_url_p1.py) · [Qualification API #6875](https://github.com/pzia/cifereca/issues/6875) |
| Kobo Vault, bibliothèque de compte | `/api/v2/library/search` peut transmettre un `product_url` public et sûr. | Collection, compte et édition exacts, y compris quand la recherche combine les deux sources. | URL publique en résultat. Aucun locator de fiche authentifiée ne passe dans C. [Fixtures de séparation](../src/plugins/provider-kobo/tests/test_kobo_search_capabilities_p1.py) |
| BnF-Prêt (session lecteur) | HTML `/resources?q=…`. `resource_url(record.resource_id)` produit `/resources/<id>` après validation. | Ressource exacte, session, identité, compte et credential ; déclaration privée du même appel, aucun relabel public. | [#7250](https://github.com/pzia/cifereca/pull/7250) : Search fédérée propriétaire → C privée self opaque → relecture et clic revalidé. [Recette SQLite](../src/plugins/provider-bnf-pret/tests/test_p0_6397_federated_private_web_c.py), [#7266](https://github.com/pzia/cifereca/pull/7266) : menu GTK authentifié 0/1/N contrôlé. Pas de preuve de navigateur connecté ni de lien prêt/édition publique. |
| Gallica (public) | OPDS EPUB/PDF documentaire, fiche canonique `https://gallica.bnf.fr/ark:/…` issue de l'ARK Atom exact et sans contradiction. SRU audio non qualifié pour C. | Singleton anonyme, identifiant Atom unique et support attesté par acquisition native, document et livraison séparés. | [#7144](https://github.com/pzia/cifereca/pull/7144) fusionnée ; [recette producteur→C→choix et restart](../src/plugins/provider-gallica/tests/test_p0_3724_public_notice_c.py) reçue, CI 44807735766 SUCCESS. ARK contradictoire : refus. [HTTP 403 observé](https://github.com/pzia/cifereca/issues/6938#issuecomment-7031280233) |
| Gutenberg (public) | Enveloppe OPDS documentaire distincte de l'offre legacy ; `alternate` HTML HTTPS canonique observé, identifiant Atom explicite unique et coordonnées non contradictoires. | ID Gutenberg exact, singleton anonyme `__public__`, collection `public_catalog`. | [#7130](https://github.com/pzia/cifereca/pull/7130) et [#7139](https://github.com/pzia/cifereca/pull/7139) fusionnées : déclaration de fiche `public_search_record_identifier` uniquement si qualifiée. [Test producteur→C→choix après éviction/restart](../src/plugins/provider-gutenberg/tests/test_p0_3724_public_notice_c.py) reçu, CI 44801145285 et 44803859043 SUCCESS. Absence/ambiguïté : pas de lien, pas de droit. [HTTP 503 observé](https://github.com/pzia/cifereca/issues/6938#issuecomment-7031280233) |
| EbooksGratuits (public) | OPDS/COPS par défaut, `alternate` HTML validé sur l'origine. Le backend HTML optionnel extrait son propre lien de détail. | ID Atom ou ID du chemin de détail HTML. | OPDS : référence C publique durable lorsque `alternate` désigne le livre exact, même après éviction/restart et changement de profil (#7212). Routes Search génériques exclues (#7179). Absence de lien : aucun choix. Identifiant contradictoire : réponse partielle sans C inventée. Backend HTML opt-in : titre/all et locator `details.php?book=<id>` qualifiés par #7444 (fixtures contrôlées, identifiant numérique unique et digest du record), autres champs non qualifiés. Le relevé terrain confirme la navigation et des offres EPUB, pas la recette C live. [Fixtures OPDS](../src/plugins/ebooksgratuits/tests/test_opds_calibre_1084.py) · [Relevé terrain](https://github.com/pzia/cifereca/issues/6938#issuecomment-7028733010) |
| Baen (public opt-in) | Produit JSON Free Library (ID natif positif et `productUrl` exact, tous deux uniques) ; carte HTML legacy. | Produit exact, réponse anonyme, URL stable dédiée ; ID historique dérivé de l'URL conservé, aucun droit. | [#7237](https://github.com/pzia/cifereca/pull/7237) : `baen.public_search_product_card` sur catégorie JSON anonyme qualifiée, C durable et choix public après éviction/restart. [Recette](../src/plugins/baen/tests/test_p0_3724_public_notice_c.py). Le GET texte libre reste partiel sans déclaration C. My Books relève du [#539](https://github.com/pzia/cifereca/issues/539). |
| BiblioAccess La Rochelle (public) | HTML `SearchResultSummary` → `Search`, ancre `/1409/Catalog/Book/{id}[/slug]`. Détail JSON-LD sur action explicite. | Tenant 1409, identifiant et URL de l’ancre retenue, session sans emprunteur. | [#7049](https://github.com/pzia/cifereca/pull/7049) : déclaration anonyme même appel, C publique durable. [#7389](https://github.com/pzia/cifereca/pull/7389) : refus meta refresh pour le zéro terminal, liens/rel contradictoires et conteneurs masqués exclus du parseur. Aucun nouveau GET ni preuve de lien issue d'une carte cachée. |
| BiblioAccess compte lecteur | Ancre fiche de la page de prêts, `biblioaccess_loans_page.py` et même garde book_id_from_url ; livraison séparée. | Compte/tenant et identifiant natif de prêt distincts de l'id fiche. | Aucun locator authentifié C générique reçu ; pas de GET ni mutation. |
| CifericaOPDS / Reference | Aucun manifeste CifericaOPDS sous `src/plugins` sur `main` au 20/11. Reference est un provider de simulation désactivé par défaut et sans capacité `search`. | Les fixtures n'attestent aucun endpoint de fiche. | Pas de recherche de fiche activable sur ce SHA. [Provider Reference](../src/plugins/provider-reference/cifereca_reference.py) · [CifericaOPDS #969](https://github.com/pzia/cifereca/issues/969) |

**Écart BiblioAccess** : le motif brut de slug ne refusait pas les séparateurs
ou dot segments encodés, doubles échappements et fichiers d'acquisition ;
le parseur d'ancre pouvait normaliser un href avant de le contrôler. Le
correctif conserve le slug légitime UTF-8 et son URL originale, refuse
les chemins ambigus et laisse les autres cartes de la page disponibles.
Tests synthétiques sur le parseur ; ne constituent pas une preuve serveur.

## Preuves pour la recherche, le détail et la réouverture

| Parcours | Preuve et réception |
|---|---|
| Numilog public, recherche puis C | Le [test LibraryService et SQLite](../src/plugins/provider-numilog/tests/test_p0_6366_federated_public_notice_c.py) relie carte produit, notice exacte, URL consolidée, éviction et réouverture. [Réception #6698, CI et GTK](https://github.com/pzia/cifereca/issues/6397#issuecomment-6972185989) |
| BnF Catalogue, recherche puis C | Le [test SRU sans vignette](../src/plugins/provider-bnf-catalogue/tests/test_public_c_sru_envelope.py) relie ARK exact, déclaration du même résultat, stockage et rétention. [Réception #6709, CI et GTK](https://github.com/pzia/cifereca/issues/6397#issuecomment-6977505306) |
| Numilog achats, enrichissement public | Le [test d'association](../src/plugins/provider-numilog/tests/test_p0_1072_public_detail_projection.py) vérifie la fiche publique projetée à partir du produit acheté exact. Le lien durable appartient au catalogue public. |
| Consolidation et clic | Le [format par source](../src/cifereca/core/notice_c_web_references.py) conserve fournisseur, source, record, édition, révision et date. Les [tests de persistance](../tests/test_core_p0_6397_c_web_reference_persistence.py) et [de choix](../tests/test_core_p0_6397_public_web_choices.py) couvrent l'éviction qualifiée et la relecture SQLite. [Garde au clic #7006](https://github.com/pzia/cifereca/issues/6397#issuecomment-7031986659) |
| Détail GTK | [Recette #6707](https://github.com/pzia/cifereca/issues/6397#issuecomment-7034926906) avec un lien Numilog. La [PR #7037](https://github.com/pzia/cifereca/pull/7037) couvre deux choix de fournisseurs synthétiques à froid et après redémarrage hors ligne. Les deux recettes vérifient la destination reçue par un lanceur de navigateur contrôlé. |
| BiblioAccess public, tenant 1409 | Le [test fournisseur et SQLite](../src/plugins/provider-biblioaccess/tests/test_biblioaccess_catalogue_provider.py) vérifie la preuve de l’ancre exacte et l’ouverture après éviction, changement de profil et réouverture de la base. [#7044](https://github.com/pzia/cifereca/pull/7044) reçoit la validation de forme du slug. [#7049](https://github.com/pzia/cifereca/pull/7049) reçoit le raccordement public. |


## Réception des lots fusionnés le 21/11

Contrôle des workflows au HEAD exact de chaque PR ; les résultats ci-dessous
sont des reçus CI, pas une validation de fournisseur distant.

| PR | HEAD testé | Gates SUCCESS | Squash dans main |
|---|---|---|---|
| [#7212](https://github.com/pzia/cifereca/pull/7212) — COPS, menu 0/1 | `d1e0feddd740d3c1dcfdf9c650f62e1f779f43a2` | [PR CI](https://github.com/pzia/cifereca/actions/runs/44869269215) | `c2c8eb4666c8e6f295105f496d7a60b86d64d3b3` |
| [#7179](https://github.com/pzia/cifereca/pull/7179) — refus Search côté COPS | `b3286543a4e97b794ca623ce878a2794869900d4` | [PR CI](https://github.com/pzia/cifereca/actions/runs/44822120932) | `192306d1fb4ce768b8bbb97d9e08ad00333c59e3` |
| [#7176](https://github.com/pzia/cifereca/pull/7176) — refus Search côté Core | `2beadb8b14f20e489ca61f3b6a37952001734d32` | [PR CI](https://github.com/pzia/cifereca/actions/runs/44820919950), [AST](https://github.com/pzia/cifereca/actions/runs/44820919931), [install](https://github.com/pzia/cifereca/actions/runs/44820919970) | `4d5b64f627ea88e5622692ed8c0134faf006e425` |
| [#7186](https://github.com/pzia/cifereca/pull/7186) — epoch strict du menu GTK | `5669c94dd2075d6c40d849ab1081289a98a3fdb5` | [PR CI](https://github.com/pzia/cifereca/actions/runs/44865184777), [AST](https://github.com/pzia/cifereca/actions/runs/44865184802), [E2E](https://github.com/pzia/cifereca/actions/runs/44865184887) | `2fca7b1a42299061f6ca8655591cdd384c1d75f6` |
| [#7037](https://github.com/pzia/cifereca/pull/7037) — deux choix GTK (fusion du 20/11) | `6317a626dd779065d494d57da30ac1bdfba63463` | [PR CI](https://github.com/pzia/cifereca/actions/runs/44726514511), [E2E](https://github.com/pzia/cifereca/actions/runs/44726514727) | `4d0d41c3c04b73c0874885f6c02cde7a5aa7d126` |

La [recette COPS](../src/plugins/ebooksgratuits/tests/test_p0_6397_public_web_choice.py)
utilise le producteur réel sur un feed anonyme contrôlé (MockTransport), une
seule lecture, le stockage SQLite et le résolveur au clic. Elle vérifie
l'absence d'entitlement/acquisition/placement, le cas sans fiche et le
refus d'un autre livre. Le cas 2+ GTK de #7037 reste synthétique et ne
qualifie pas le navigateur authentifié.

## Réception complémentaire au 21/11 — sources et clics désormais reçus

| PR | HEAD testé | Gates SUCCESS | Commit de fusion |
|---|---|---|---|
| [#7237](https://github.com/pzia/cifereca/pull/7237) — Baen JSON public | `9082e502fd48d5da23d71969bc2db607e51814b9` | [PR CI](https://github.com/pzia/cifereca/actions/runs/44873194073) | `dd1cc345ebf586c98a06442398de23c39e57a63d` |
| [#7250](https://github.com/pzia/cifereca/pull/7250) — BnF-Prêt privé | `e2fde865c16c8b523fa9f821839795b93940fd19` | [PR CI](https://github.com/pzia/cifereca/actions/runs/44876027438), [AST](https://github.com/pzia/cifereca/actions/runs/44876027477), [install](https://github.com/pzia/cifereca/actions/runs/44876027394), [E2E](https://github.com/pzia/cifereca/actions/runs/44876027491) | `6ae3bc38ce452aa1e8379ebf9798a2c8340dac66` |
| [#7266](https://github.com/pzia/cifereca/pull/7266) — menu authentifié GTK 0/1/N | `ccae76f04758f20f947bd1af2e6416133dffaea9` | [PR CI](https://github.com/pzia/cifereca/actions/runs/44886360567), [E2E](https://github.com/pzia/cifereca/actions/runs/44886360467) | `4eb696c8a69d0f6f34efd1b72d45179bc42a9d94` |
| [#7372](https://github.com/pzia/cifereca/pull/7372) — reçu privé type-first | `ba6b7e530f7d58589d59c5257aaea952d0105880` | [PR CI](https://github.com/pzia/cifereca/actions/runs/44922574320), [AST](https://github.com/pzia/cifereca/actions/runs/44922574329), [install](https://github.com/pzia/cifereca/actions/runs/44922574397) | `88611a9d10a3fc7f4d53a2088a7d6de306ef3752` |
| [#7389](https://github.com/pzia/cifereca/pull/7389) — HTML BiblioAccess | `deea9862b3a5d823b7cf941e5c39721f2c5c1595` | [PR CI](https://github.com/pzia/cifereca/actions/runs/44920217228) | `aa45b2170432cb21cb8669394f73a8e9fa0a471e` |

La recette privée BnF-Prêt vérifie notamment l'absence d'écriture de C
sur résultat recopié, révoqué ou batch non admis, refus d'un lecteur
étranger, et un seul échange de transport contrôlé. Elle ne donne
aucune session au navigateur externe. Les données C privées restent
distinctes des C publiques et des droits A/B.

**Reliquats précis** (pas de clôture générale) :

- Paris et Kobo : le champ de fiche est disponible dans les résultats,
  mais la destination et l'autorité exactes pour une admission C
  publique anonyme ou privée propriétaire restent à recevoir ; ne pas
  promouvoir une session authentifiée en singleton public.
- Numilog achats et BiblioAccess prêts : aucun locator de fiche
  authentifiée générique qualifié pour C, pas de relation catalogue-prêt
  déduite. L'enrichissement public Numilog #1072 reste acquis.
- EbooksGratuits HTML : titre/all reçu, autres champs non qualifiés ;
  Baen GET texte libre et les autres parcours non attestés restent sans
  admission C web positive.
- Matrice recherche/liste/détail par source, garde de clic et recette
  réelle d'un navigateur nécessitant authentification : qualification
  distincte des tests synthétiques, en coordination avec #6875.
- #4980 conserve la réception transverse intégrée, aucune nouvelle
  collecte ne doit être entreprise pour répéter les preuves reçues.

Aucun nouveau GET, extension d'autorité ou droit A/B. Les reçus exacts
ci-dessus remplacent seulement les anciennes mentions « non reçu »
concernant Baen catégorie JSON et BnF-Prêt privé, pas les autres limites.


## Réceptions complémentaires au 22/11

| PR / parcours | HEAD exact | Gates SUCCESS (runs) | Squash intégré |
|---|---|---|---|
| #7130 Gutenberg OPDS | `35b94ae29bcdec4034adceb98ffa446b0299a7dd` | CI 44801145285 | `f247ef913171d659a38a5f991d030315b2decaab` |
| #7139 Gutenberg | `64e6208fcf6bd7a7fbb7d200865379c2b0e267c1` | CI 44803859043 | `57b4c95ab7d92d33f9b5737643aff48db3978563` |
| #7144 Gallica OPDS | `ae994fb2b9fc9f16dcf1639e091df65a46f7967a` | CI 44807735766 | `250d1d2d775d759743705924e9bc7ad49b49bc01` |
| #7444 EbooksGratuits HTML titre/all | `1382e3175642c11e9c5d4dbcd2902888a7944f8e` | CI 44947832515, AST 44947832511, install 44947832535, GTK 44947832528 | `e9a5e2bf08af51df7ee2a7e261632d22e5ec4cfe` |
| #7478 distinction des bibliothèques | `835d35c51a4feb56700255d7ff16a566cddb5996` | CI 44991869559, AST 44991869586, GTK 44991869577 | `4e30ef4411d52382168acc43d36548c19f0e70ed` |
| #7463 Emprunts/Historique sans miniature | `d97393694c6f55165861ea75b3abb5bbb018c126` | CI 44991943216, AST 44991943219, install 44991943161 | `ec1e16c0b387f06a245b651f5ef9188b5e8935c7` |
| #7484 comptes privés distincts | `1bd2209c3586367decda26ea5987fef04357627f` | CI 44992140058, AST 44992140062, install 44992140043 | `1fad2eb4de260ca7f547bb1bda765d320619c628` |
| #7507 révocation indépendante public/auth | `93a8b2cf68243344590ad7eaca80b5e7191da0b6` | CI 44993212271, AST 44993212127, GTK 44993212099 (relance, 54/54) | `0211188b37938e0b8a88444adbd478aadf33aa12` |

Réceptions CI/CD et FOCUS détaillées dans [#6397](https://github.com/pzia/cifereca/issues/6397).
Les parcours Emprunts/Historique sans couverture conservent la fiche publique
sur le DocumentRef exact, sans créer de droit. Le choix entre comptes privés
reste scoped ; un nouveau menu révoque les anciennes lectures et activations
avec générations distinctes public/authentifié. Ces réceptions sont internes
et ne qualifient ni une destination protégée non démontrée ni l'authentification
du navigateur externe. Les limites Paris/Kobo, Numilog achats/BiblioAccess
prêts et la recette agrégée restent explicites, sans collecte supplémentaire
imposée au lot externe #6875 non bloquant.

## Réception des cardinalités et corrections ciblées — 22/11

| PR | HEAD testé | Gates SUCCESS | Squash intégré |
|---|---|---|---|
| #7524 — trois fournisseurs, une C, trois fiches | `09ccedb5b92ce586d40151d5936ae3d9f7141596` | CI 44998509736, E2E 44998509729 | `e27582e20f579ae32ff2e3453ba165e371237b4b` |
| #7533 — correction d'un record parmi trois | `3233669f87052c7e23c5494fe1e320dbe32cda58` | CI 44999238448 | `22224f453fc2670ad2294ee9d461ec4be7f2a594` |

Les blobs des trois fichiers de ces PR sont identiques au HEAD testé,
au squash et sur main. Le témoin synthétique démontre la cardinalité
Search/SQLite, le choix GTK et le maintien des fiches après éviction
et redémarrage ; la correction ciblée refuse l'ancienne option sans
supprimer les deux autres en attendant la recomposition.

**Critère interne restant, distinct de #6875 :** le producteur privé
[`_source_key`](../src/cifereca/core/notice_c_private_search_web.py)
incorpore actuellement l'identité au digest de l'édition et à la
NoticeSourceKey. La recette #7484 concerne deux comptes d'un même
profil ; elle ne démontre pas une observation unique lorsque le même
compte est déclaré dans deux profils. Ne pas supprimer l'identité du
scope de résolution au clic pour dédupliquer l'observation. L'admission
d'un groupe public par Search refuse par ailleurs les relations mixtes
privées (#7502) ; une preuve de relation documentaire ne transmet jamais
les permissions du compte. Le contrat de séparation entre observation
réutilisable et autorité de clic reste à recevoir avant fermeture.
Les limites de qualification externe précédentes restent reportées sous
#6875 ; aucune nouvelle capture ni droit ne sont demandés.
