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
| Paris (catalogue via session) | `FriendlyUrl` de Search, `cifereca_paris_normalize.py`. | RscBase/RscId/scénario + credential ; ne pas assimiler édition physique/PNB. | Déclaration publique séparée retire friendly_url ; pas d'admission durable C web démontrée. Tests scopes/parseur `test_p1_3415_search_response_facets.py`, frontière étrangère `test_core_p0_4673_paris_foreign_search.py`. Auth/locator non qualifiés. |
| Kobo (public/privé via Vault) | `product_url` via `_public_product_url` du catalogue, pas fallback vers route d'acquisition. | ID de produit et revision, collection/account distincts. | `test_kobo_product_url_encoded_acquisition_p0.py` ; lien de ligne qualifiable, aucun témoin C web same-call prouvé ici. API externe #6875. |
| BnF-Prêt (session lecteur) | `resource_url(record.resource_id)` dans projection Search. | Identifiant de ressource ≠ prêt ni édition BnF SRU. | Pas de preuve C web publique/auth ni locator reçus ici ; qualifier source et clic. |
| Gallica (public) | OPDS friendly, SRU audio ARK construit depuis identifiant. | Notice OPDS vs ARK audio ; séparer support. | Pas de déclaration same-call web démontrée ; #1917 tests et #6938 refus 403, pas preuve d'absence. |
| Gutenberg (public) | OPDS `friendly` projeté par parseur. | Notice/identifiant déclaré et rôle du lien. | Pas de preuve C web démontrée ; 503 #6938, pas de faux zéro. |
| EbooksGratuits (public) | OPDS alternate ; HTML optionnel detail ; feed/fetch/EPUB distincts. | ID Atom ou ID détail du chemin HTML, offre séparée. | #2460 et `test_opds_web_vs_acquisition_p1_2460.py` qualifient séparation fiche/EPUB ; pas de témoin web C reçu. |
| Baen (public opt-in) | JSON Free Library produit, carte HTML historique qualifiée. | Produit exact et URL dédiée ; pas droit. | #2333 distinct, pas de preuve de persistance C web. |
| BiblioAccess La Rochelle (public) | HTML SearchResultSummary → Search → ancre `/1409/Catalog/Book/{id}[/slug]`, détail JSON-LD sur action explicite. | Tenant 1409 et id de l'ancre, session sans emprunteur. | `test_biblioaccess_catalogue_provider.py` ; garde de chemin/slug durcie dans ce lot. Provider ID contient un point et l'enveloppe same-call n'est pas implémentée ici ; ne pas relâcher le format de preuve Core pour le promouvoir. |
| BiblioAccess compte lecteur | Ancre fiche de la page de prêts, `biblioaccess_loans_page.py` et même garde book_id_from_url ; livraison séparée. | Compte/tenant et identifiant natif de prêt distincts de l'id fiche. | Aucun locator authentifié C générique reçu ; pas de GET ni mutation. |
| CifericaOPDS / Reference | Catalogue fixture ou candidat HTTP distinct ; Reference synthétique. | Capacités fixture non transférables au HTTP. | #969 P2 ; URL fiche/C non vérifiées dans cet audit, aucun PASS. |

**Écart BiblioAccess** : le motif brut de slug ne refusait pas les séparateurs
ou dot segments encodés, doubles échappements et fichiers d'acquisition ;
le parseur d'ancre pouvait normaliser un href avant de le contrôler. Le
correctif conserve le slug légitime UTF-8 et son URL originale, refuse
les chemins ambigus et laisse les autres cartes de la page disponibles.
Tests synthétiques sur le parseur ; ne constituent pas une preuve serveur.

**Reliquats** : les références C authentifiées et leur revalidation de compte,
les producteurs non admis et leurs preuves d'édition, ainsi que la matrice
exhaustive des fixtures et gates par parcours restent ouverts. Aucun
nouveau GET, aucune extension d'autorité, aucune qualification de navigateur
authentifié. Ce document n'est pas un reçu de clôture #6397.
