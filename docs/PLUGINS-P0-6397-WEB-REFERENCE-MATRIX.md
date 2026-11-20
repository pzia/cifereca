# #6397 — Matrice de continuité des fiches web (20/11/2026)

Audit statique des chemins producteurs courants, distinct de la recette GTK
et des captures réseau. Un lien de ligne/détail ne prouve pas son admission
dans C. Pour C public, le Core exige le témoin same-call anonyme, le
producteur de la preuve qualifié et les coordonnées exactes ; les options
relisent la contribution et sa fence à l'ouverture. Aucune URL signée,
session, acquisition, ni équivalence d'édition déduite.

| Provider / source | Champ et parcours actuel | Identité et portée | C / ouverture / preuve restante |
|---|---|---|---|
| Numilog catalogue public | Carte produit `friendly_url` et preuve `public_search_product_card`, fiche JSON-LD `public_product_jsonld` ; HTML observé et parseur public. | Produit exact, session anonyme, collection public_catalog. | Enveloppe same-call et C public ; choix de lien, éviction, restart. Tests `test_p0_6366_federated_public_notice_c.py` et recette mono-lien ; multi-options #7037 non présumée reçue. Formulaire avancé #1886 distinct. |
| Numilog Mes livres (auth) | `public_url` de carte bibliothèque (preuve `library_title_link`), redirect Readzis qualifié séparément ; détail explicite. | lid/compte/credential exact ; lien public stable éventuel, pas droit du navigateur. | Enrichissement d'achat #1072 distinct ; la projection publique de la session privée retire URL. Pas de locator C authentifié générique reçu. |
| BnF Catalogue général (public) | SRU DC identifiant ARK → `catalogue.bnf.fr/{ark}`, `public_search_record_identifier`. | ARK de la notice, session __public__ canonique, pas prêt. | Enveloppe same-call, même absence de couverture ; `test_public_c_sru_envelope.py`. Qualification live titre/pagination #6938 ; lien individuel non certifié par un simple GET SRU. |
| Paris (catalogue via session) | `FriendlyUrl` dans `Results[]` de `POST Search.svc/Search`. Les réponses historiques attestent les scénarios `CATALOGUE` et `EBOOKS`. [Relevés #3415](https://github.com/pzia/cifereca/issues/3415#issuecomment-6587935035) | RscBase, RscId, scénario et credential. Le scénario ne détermine pas à lui seul le support. | La déclaration publique retire `friendly_url`. La fixture de parsing ne qualifie ni sa destination par édition ni le mode d'accès. [Test du parseur](../src/plugins/provider-paris/tests/test_p1_3415_search_response_facets.py) · [Frontière inter-profils](../tests/test_core_p0_4673_paris_foreign_search.py) |
| Kobo Vault, catalogue public | `/api/v2/catalog/search` fournit `product_url`, validé par `_public_product_url`. | ID de produit et révision. `scope=catalog` exclut la bibliothèque de compte. | URL en résultat lorsque qualifiée. La déclaration publique retire `friendly_url`. [Fixtures URL produit](../src/plugins/provider-kobo/tests/test_kobo_public_product_url_p1.py) · [Qualification API #6875](https://github.com/pzia/cifereca/issues/6875) |
| Kobo Vault, bibliothèque de compte | `/api/v2/library/search` peut transmettre un `product_url` public et sûr. | Collection, compte et édition exacts, y compris quand la recherche combine les deux sources. | URL publique en résultat. Aucun locator de fiche authentifiée ne passe dans C. [Fixtures de séparation](../src/plugins/provider-kobo/tests/test_kobo_search_capabilities_p1.py) |
| BnF-Prêt (session lecteur) | HTML `/resources?q=…`. `resource_url(record.resource_id)` produit `/resources/<id>` après validation. | Ressource exacte. Le détail lie provider et credential. | Fiche en résultat. Son mode d'accès et sa persistance C restent à qualifier. [Fixtures DOM](../src/plugins/provider-bnf-pret/tests/test_web_dom.py) |
| Gallica (public) | OPDS EPUB et SRU audio. Le parseur officiel rattache la fiche `gallica.bnf.fr/ark:/…` à l'ARK de la notice. | Identifiant qualifié par branche et support. | Lien en résultat, sans preuve C web. [Fixtures ARK/OPDS](../src/plugins/provider-gallica/tests/test_opds_audio_regressions.py) · [HTTP 403 observé](https://github.com/pzia/cifereca/issues/6938#issuecomment-7031280233) |
| Gutenberg (public) | OPDS `friendly` ou fiche `/ebooks/<numéro>` issue de l'identifiant extrait. | Identifiant Gutenberg et rôle de fiche. | Lien en résultat, sans preuve C web. [Fixtures détail OPDS](../src/plugins/provider-gutenberg/tests/test_bounded_detail_opds_6590.py) · [HTTP 503 observé](https://github.com/pzia/cifereca/issues/6938#issuecomment-7031280233) |
| EbooksGratuits (public) | OPDS/COPS par défaut, `alternate` HTML validé sur l'origine. Le backend HTML optionnel extrait son propre lien de détail. | ID Atom ou ID du chemin de détail HTML. | Fiche lorsqu'elle est présente, sans preuve C web. Le relevé terrain confirme la navigation et des offres EPUB. [Fixtures OPDS](../src/plugins/ebooksgratuits/tests/test_opds_calibre_1084.py) · [Relevé terrain](https://github.com/pzia/cifereca/issues/6938#issuecomment-7028733010) |
| Baen (public opt-in) | Produit JSON Free Library et carte HTML à href qualifié. | Produit exact et URL dédiée. | Fiche en résultat, sans preuve C web. L'accès personnel My Books relève du [#539](https://github.com/pzia/cifereca/issues/539). [Fixtures HTML](../src/plugins/baen/tests/test_public_improvements.py) |
| BiblioAccess La Rochelle (public) | HTML `SearchResultSummary` → `Search`, ancre `/1409/Catalog/Book/{id}[/slug]`. Détail JSON-LD sur action explicite. | Tenant 1409, identifiant et URL de l’ancre retenue, session sans emprunteur. Un titre d’un autre slug ne qualifie pas cette URL. | [#7049](https://github.com/pzia/cifereca/pull/7049) relie la déclaration anonyme du même appel à l’observation C et à une fiche publique durable pour les URL admissibles. Les titres génériques et contradictoires restent exclus. Les slugs encodés restent soumis à la garde URL Core. |
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

**Reliquats** : les références C authentifiées et leur revalidation de compte,
les producteurs non admis et leurs preuves d'édition, ainsi que la matrice
exhaustive des fixtures et gates par parcours restent ouverts. Aucun
nouveau GET, aucune extension d'autorité, aucune qualification de navigateur
authentifié. Ce document n'est pas un reçu de clôture #6397.
