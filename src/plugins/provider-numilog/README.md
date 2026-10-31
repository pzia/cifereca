## 0.1.79 — livraison autorisée par le tableau Links (#2024 P0)

La confirmation liée au compte et à la carte de l'achat est
l'**autorité de provenance** du lien : le parseur n'impose plus une
liste figée de domaines, handlers ou formats de jetons à la valeur
`link`. Pour une édition avec alternatives `ADOBE` / `LCP`, il
sélectionne uniquement l'unique entrée `ADOBE`; la variante
`LCP` n'est pas interrogée. Pour `NOT_SPECIFIED` / `EPUB`,
le téléchargement direct reste validé comme EPUB.

Les conditions de sélection restent strictes : origine HTTPS Numilog
de la **page de confirmation**, `readBook` ou href de la carte achetée,
lien explicite `ModalDownload`, identifiants produit/exemplaire
concordants, entrée `Links` littérale unique par technologie et
contradictions de query `pid/productid/DRM` refusées.
Aucune URL de la page seule, d'une fiche publique ou d'un script
non lié n'autorise une acquisition.

L'URL de livraison sélectionnée est une **capacité éphémère** : aucune
publication de la query, aucun lien persistant, aucun fallback LCP.
Avant requête sur une nouvelle destination, le transport impose HTTPS,
pas de credentials/fragment/port non standard ni d'IP littérale ou
de nom local/réservé, et rejette toute résolution DNS privée/non globale.
Sur les destinations attestées hors origine de la bibliothèque, les
cookies de session Numilog sont supprimés ; chaque redirection HTTPS
est vérifiée avant de poursuivre (limite 12 sauts). La vérification DNS
préalable n'est pas un transport DNS-pinné et ne prétend pas empêcher
à elle seule toutes les variantes de DNS rebinding.

Les résultats reçus sont **inspectés**, et non déduits du libellé
ADOBE/EPUB : ACSM seulement après identité + broker ADEPT disponibles,
EPUB non protégé seulement après validation structurelle.
Les tests MockTransport/DNS simulés couvrent plusieurs fournisseurs,
l'absence de whitelist, la protection des cookies, les duplications,
des destinations non publiques, les redirections et les éditions
étrangères. Aucune signature privée n'a été utilisée. Cela ne prouve
pas une livraison réelle, ni l'échec du worker automatique.


---

## 0.1.78 — livraison EPUB direct signée et liée à l'achat (#2024 P0)

Le fournisseur publie, sur certaines confirmations sans streaming, un
`Links` littéral avec `DrmTechnlology='NOT_SPECIFIED'`,
`format='EPUB'` et un lien de livraison signé sur
`services.numilog.com/v4.0/HandlerDownloadLinks/HandlerDilicomDownloadLink.ashx`.
Le parseur Numilog gère désormais explicitement cette **autre forme** de
livraison : `OwnedDelivery.delivery_kind=epub_direct`, sans exécuter le
JavaScript ni reproduire une URL réelle. Les paramètres de la signature sont
acceptés uniquement sur le domaine et chemin HTTPS exacts, avec noms
`productid/idcmd/date/hash`, bornes et unicité ; `productid` correspond
au ProductId de `Links`, **jamais** au `lid` de MesLivres.

Le GET est autorisé uniquement après preuve d'achat rechargée et compte/
identité exacts, `ModalDownload(ProductId, IndexExemplaire)` de la carte,
référence de confirmation concordante si publiée et unicité des candidats.
L'absence de lien streaming sur la confirmation est acceptée après **302
direct** du `readBook?m=dwld` sélectionné ou après lien de confirmation
`disp=simple` présent dans cette seule carte de `MesLivres`. Une simple
page de commande isolée, une autre édition, un doublon ou un script seul ne
suffisent pas. Le lien signé demeure éphémère, jamais dans SearchResult,
journal, réception durable ni diagnostic. Un EPUB valide est publié via
la branche ZIP/EPUB existante, sans broker ADEPT ; ACSM et LCP conservent
leurs restrictions.

Tests synthétiques `test_numilog_not_specified_epub_confirmation_p0_2024.py` :
4 achats, 302/confirmation ou lien explicite de la carte, faux compte/édition,
lien signé altéré, mauvais MIME, EPUB corrompu, redirection interdite,
formulaire de connexion global et absence d'acquisition ADEPT.
La prise en charge d'un format ne valide **pas** la récupération chez un
utilisateur donné : la recette réelle expurgée et le rapprochement achat/
Artifact sans supprimer ni reconsommer un ACSM restent ouverts sous #2024.

---

## 0.1.77 — invalidation des résultats publics après reconnexion (#1886 P0)

Les parcours publics `browse_public_search` et `follow_public_link`
vérifiaient déjà la génération de navigation lors d'une requête longue.
Le chemin générique `search()` et l'enrichissement direct
`enrich_public_detail()` ne faisaient pas cette vérification **après**
l'utilisation du cache et la projection : un callback terminé pendant
`close()` ou `connect()` pouvait encore fournir une ancienne notice.

Ces deux parcours capturent désormais la génération de session
**avant la validation des arguments**, la revérifient avant tout GET
pour refuser une reconnexion survenue pendant la validation, puis
la revérifient **juste avant de restituer la réponse**. Sinon, ils
refusent le résultat avec le message fixe
`Numilog public : navigation_invalidated`, sans URL ni corps HTTP.
C'est une garde de cycle de vie, **pas une transaction UI** : si une
fermeture survient après le retour, le Core/UI doit lui-même jeter ses
callbacks obsolètes. Aucun nouvel HTTP, aucune recherche implicite et
aucune modification des achats, droits, cookies ou ACSM.

Tests entièrement hors réseau :
`test_numilog_public_session_generation_guard_p0.py` couvre fermeture
et reconnexion en fin de chargement, fin de projection et après un
**cache hit**, ainsi que les parcours normaux et l'absence de GET
supplémentaire. Le P0 #1886 reste ouvert pour la qualification de la
recherche avancée réelle et l'intégration UX.

## 0.1.76 — genres, mots-clés et séries littérales JSON-LD (#1072/#1886 P0)

Sur une **fiche publique explicitement enrichie et identifiée**, le parseur
`Book` JSON-LD conserve désormais ses déclarations `genre`,
`keywords`, `isPartOf` (série `BookSeries` ou `CreativeWorkSeries`)
et `alternativeHeadline`, uniquement lorsque des textes simples,
typés le cas échéant, bornés et sans caractère de contrôle sont présents.
La sélection `isPartOf` n'infère jamais une série à partir d'un autre
produit ; chaque champ garde sa preuve `public_product_jsonld_Book` et
l'URL du produit.

Les clés conservées sont `genres` (liste), `public_keywords` (liste de
libellés **littéraux**, sans découpage des virgules), `public_series_label`
et `public_alternative_headline`. Ce dernier n'est **pas** assimilé
automatiquement à un sous-titre d'édition. La validation côté cache
refuse les types, valeurs/URL et preuves contradictoires.

Les filtres `refine_loaded_page(page, genre=...)`,
`refine_loaded_page(page, series=...)` et
`refine_loaded_page(page, keyword=...)` fonctionnent uniquement sur
les fiches **déjà chargées explicitement** avec
`enrich_loaded_page_product`. Ce sont des correspondances
**positives seulement** : pour un champ manquant, une autre valeur,
une fiche non consultée ou une provenance altérée, le livre reste
`inconnu`, pas `absent du catalogue`. Aucun total filtré
global ni requête serveur multi-attributs n'est annoncé ; aucune
récupération en masse de fiches n'est effectuée.
Avant de restituer un résultat positif, le filtre revalide également les
identifiants typés, libellés HTML et offres commerciales annexes présents
dans le cache de détail. Une preuve annexe modifiée ou une URL de livraison
intruse invalide la correspondance entière.

**Important :** ces mentions décrivent uniquement le **produit
commercial**, non la classification locale d'un ouvrage, la variante
EPUB/PDF achetée, le droit `owned`, le format acquis ou un ISBN
numérique attesté. Aucun GET automatique, aucune mutation de droits
ou d'artefacts. Les fixtures publiques actuelles n'attestent pas la
présence effective de ces champs sur Numilog ; les exemples de tests
JSON-LD sont synthétiques. Tests : `test_numilog_public_editorial_subjects_p0.py` et
`test_numilog_public_book_topic_local_filter_p0.py`.
Le ticket reste ouvert pour captures et recette réelle.

## 0.1.75 — budget de profondeur/largeur du DOM public (#1886 P0)

Une fiche peut respecter la limite HTTP de **1 Mio** tout en saturant
les seuils du parseur commun (**100 niveaux / 25 000 nœuds**) avec
des nœuds sans contenu. Les parcours récursifs de `extract_public_metadata` étaient
jusqu'ici exposés à une erreur de récursion avant de vérifier les
libellés de la fiche.

Le plugin qualifie désormais le DOM **avant** ces parcours, avec une
borne indépendante de **80 niveaux et 12 000 nœuds**. Une page
disproportionnée est refusée par `PublicMetadataError` (message fixe),
sans erreur de récursion brute et sans prétendre que les métadonnées
sont absentes du produit. L'analyse des identifiants, les droits
`owned`, les URLs de livraison et le chemin HTTP ne changent pas.

Tests hors réseau sur fiche publique expurgée et HTML de profondeur/
largeur adversariales :
`tests/test_numilog_public_visible_dom_bounds_p0.py`.
Cette garde ne constitue ni validation du contrat de recherche avancée
ni autorisation de crawl Numilog.

## 0.1.74 — attribution stricte des rôles de fiche Numilog (#1072 P0)

Les champs publics JSON-LD `translator`, `illustrator` et `contributor`
n'attestent désormais un rôle que si chaque objet de la liste fournit
explicitement `@type=Person` ou, pour les rôles éligibles,
`@type=Organization`. Les URI officielles
`http://schema.org/Person` et `https://schema.org/Person` (ou
`Organization`) sont acceptées avec leur équivalent court.
Une simple map `{"name": "..."}`, même accompagnée d'un URL produit
et d'un titre valide, est **omise** : elle n'est ni un contributeur
attribuable ni un auteur substitutif. Listes hétérogènes, types imbriqués
ou types non attendus restent non probants.

Le rejet concerne seulement les **champs de rôles optionnels** :
il ne modifie ni la preuve de possession, ni un ISBN d'édition achetée,
ni le téléchargement, ni les auteur(s) déjà qualifiés. Tests hors
réseau `test_numilog_owned_public_editorial_roles_p0.py` avec
fiches JSON-LD synthétiques ; la présence réelle de ces propriétés
sur Numilog est toujours à confirmer sous #1072.

## 0.1.73 — intégrité des filtres locaux auteur et format (#1886 P0)

Le filtre facultatif `refine_loaded_page` travaille sur une **page
commerciale déjà chargée**, parfois issue d'un cache recopié. Les
sélecteurs HTML et leur preuve ne suffisent pas si un appelant modifie
ensuite `metadata.authors` ou `metadata.format_labels`.

Avant toute correspondance positive, le plugin revérifie les listes,
les caractères Unicode de contrôle/format/surrogate, les limites
observées (32 auteurs ou 10 formats, 1 000/80 caractères), et la cohérence
entre noms détaillés et affichage `author`, ainsi qu'entre labels de
formats et `SearchResult.format` dérivé du parseur. Une incohérence
rend ce champ **inconnu** et n'autorise aucun faux résultat exhaustif.
Le filtre reste sans GET, sans accès privé ni achat. Le produit
commercial multi-format n'est jamais converti en variante acquise.

Tests synthétiques : `test_numilog_public_filter_cache_proof_p0.py`.
Les champs de recherche avancée serveur (titre/auteur/EAN/format) restent
à qualifier et ne sont pas annoncés par ce filtre.

## 0.1.72 — rattachement d'identité stable pendant un enrichissement (#1072 P0)

Dans les parcours **explicitement demandés** `enrich_owned_public_listing`,
`enrich_owned_public_detail` et `enrich_owned_direct_public_detail`,
le plugin fixe l'identité propriétaire du compte **si le broker public
`identities` est configuré**, puis la revérifie après la lecture privée
`Mes Livres`, au retour de la recherche commerciale et au retour de la fiche
produit. Un compte supprimé/réassigné ou un broker devenu inaccessible
arrête l'opération avec un **code textuel fixe**, sans publier une ancienne
notice sous le nouveau propriétaire ni recopier l'exception du broker.

Cette garde ne touche ni le contrat de possession, ni l'autorisation
d'acquisition ACSM, ni les URL privées. Elle n'ajoute aucune requête HTTP
au chemin nominal. Les anciens clients autonomes sans broker gardent leur
contrôle strict `credential_id` + `record_id` + inventaire privé rafraîchi.
Un changement **après** le dernier contrôle reste du ressort de la
génération de sélection/identité du CoreUI (aucune transaction d'identité
n'est prétendue). Tests purement synthétiques :
`tests/test_numilog_owned_enrichment_identity_race_p0.py`.

## 0.1.71 — rôles et mentions éditoriales de fiche Numilog (#1072 P0)

Une **fiche publique explicitement demandée**, associée à un produit
`Mes Livres` via son URL canonique et le titre de l'achat fraîchement
vérifié, peut désormais conserver les déclarations JSON-LD `Book` :
`translator`, `illustrator`, `contributor` sous des clés **distinctes**
(`translators`, `illustrators`, `contributors`), ainsi que
`bookEdition` (`public_edition_label`), `numberOfPages`
(`public_page_count`) et `typicalAgeRange` (`public_age_range_label`).
Les personnes/organisations sont des objets nommés et bornés ; une valeur
malformée est omise, et toute mutation non qualifiée du cache est refusée
par `validated_public_jsonld_fields`. Une provenance est conservée par champ
dans `public_detail_enrichment.public_jsonld.field_evidence`.

**Sémantique stricte** : il s'agit de métadonnées du **produit commercial**,
pas d'une preuve de la variante EPUB/PDF achetée, d'un ISBN numérique, d'un
droit d'acquisition, ni d'un âge recommandé automatiquement. La liste des
traducteurs/illustrateurs n'est jamais réécrite en auteurs. Aucun HTTP
supplémentaire n'est ajouté à `search/library/holdings`, aucun
téléchargement ni synchronisation n'est déclenché. La présence réelle
de ces propriétés sur Numilog **n'est pas attestée** par les deux fixtures
produit actuelles ; leurs cas positifs sont **synthétiques** et suivent
le contrat [Schema.org Book](https://schema.org/Book).

Tests hors réseau : `test_numilog_owned_public_editorial_roles_p0.py`.
Les étapes de recette commerciale et la qualification exacte de
l'édition achetée demeurent ouvertes dans #1072.

## 0.1.70 — filtre local de langue publique attestée (#1886 P0)

`NumilogPublicSession.refine_loaded_page(page, language="fr")` accepte
désormais une **correspondance positive** de langue à partir d'une fiche
produit explicitement enrichie, à condition que son URL, son titre, son
`Book` JSON-LD et la provenance du champ soient vérifiés. Si la page
publie aussi le libellé visible « Langue », cette seconde preuve peut
être utilisée après validation stricte du DOM et des identifiants.

La carte de recherche `brSimple` **ne publie pas la langue** : sans
enrichissement explicite (ou lorsque la langue ne correspond pas),
le résultat est `inconnu`, jamais « absent du catalogue ». Les
totaux publics ne sont pas remplacés par des totaux filtrés, aucun
GET n'est lancé par ce filtre, aucune page suivante n'est
parcourue et aucun champ serveur `field=language` n'est annoncé.
Le titre, ISBN papier/EPUB, offre et droit `owned` restent distincts.

Tests hors réseau : `test_numilog_public_attested_language_p0.py`
et régression du filtre local historique.

# Numilog — provenance des URL de livres (0.1.16)

## 0.1.55 — navigation facettes sans perte silencieuse (#1886)

La navigation GET de la recherche publique `brSimple` conserve chaque
facette **déjà appliquée**, à l'exception de l'unique facette
explicitement remplacée par le lien observé. Un lien thème/prix/langue/
éditeur qui retire implicitement une autre sélection, change plusieurs
filtres à la fois ou impose `GRID_PAGE > 0` au moment du filtrage est
refusé par code fixe `invalid_facets`. Les transitions qui défiltrent
une dimension ne sont pas devinées sans preuve du parcours amont.

La sélection d'un filtre repart de la première page ; la pagination
normale conserve ses paramètres comme avant. Les tests sont offline,
sur cartes publiques expurgées et liens synthétiques, sans nouveau
transport, cookie, achat, accès privé ou preuve de disponibilité live.
Les options serveur auteur, ISBN et recherche avancée restent non attestées.


## 0.1.51 — achats sans lecture en ligne : confirmation ACSM

Certaines éditions achetées ne disposent **pas** d'un lien
`Lire en ligne (Streaming)` dans la page `ConfirmationPaiement.aspx`.
L'absence de `readBook?m=stream` ne signifie ni absence d'achat,
ni absence de lien ACSM Adobe. Le fournisseur peut afficher seulement
`ModalDownload(ProductId, IndexExemplaire)` et le tableau `Links`.

Le fournisseur accepte dorénavant ce cas **seulement** lorsque Cifereca
a lui-même suivi un `readBook?m=dwld` provenant d'une fiche achetée
et obtenu une **redirection HTTP 302 directe** vers
`ConfirmationPaiement.aspx?ref=<opaque>&disp=simple`.
La page doit alors attester une seule carte marchande, un seul bouton,
un seul produit/exemplaire sur toutes les alternatives JavaScript, un
ACSM Adobe unique et une éventuelle référence de formulaire cohérente.
Le lien streaming, lorsqu'il existe, continue d'être vérifié comme
preuve supplémentaire ; aucun lien streaming n'est utilisé pour livrer l'EPUB.

Refus si page multicarte, second bouton, autre produit/copie, lien readBook
contradictoire ailleurs, URL ACSM concurrente, redirection indirecte,
page de confirmation ouverte sans preuve 302, ou `ref` incohérent.
Aucune construction d'URL de commande, nouveau GET réseau, affaiblissement
du contrôle de propriété/identité ou divulgation de jeton.
Tests MockTransport purement synthétiques dans
`test_numilog_no_stream_confirmation_p0.py`.

**Portée de la preuve :** le téléchargement manuel ACSM avait déjà réussi
sur une édition qui proposait le streaming ; la cause de l'échec de la
sauvegarde automatique est encore à confirmer par une trace de recette
sur une édition sans streaming. L'observabilité de la tâche automatique
fait l'objet d'un suivi séparé.


## Audit P2 #764 — 11 octobre 2026

**Preuves sur les pages publiques officielles**

- [Blackbone — Coltan Song, tome 1](https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook)
  fournit une indication « Roman ado dès 15 ans », résumé, auteurs,
  marque, parution, langue, EAN papier et EAN EPUB distincts.
- [Le Piège de Sainte-Soline](https://www.numilog.com/1956345/le-piege-de-sainte-soline--megabassines--la-lutte-pour-l-eau.ebook)
  publie également résumé, éditeur, parution, ISBN/EAN séparés par
  format et genre. **Les notices privées « Mes Livres » ne fournissent
  pas nécessairement ces champs.**

**Écart** : le provider authentifié Numilog ne projette actuellement
que titre, couverture éventuelle, identifiant de compte et référence de
lien public ; aucun auteur/ISBN/âge issu du site public n'est récupéré.
Associer une fiche publique à un livre possédé exige une preuve
bibliographique solide sur **la même édition numérique**, et pas un
simple rapprochement de titre ou d'ISBN papier.

**Correctif strict de cette PR** : protéger la provenance des liens
publics issus de « Mes Livres » :

- URL reconnue `/<identifiant>/<slug>.ebook` sur origine Numilog, ou
  anciennes formes compatibles `/livres/<slug>` et `/ebooks/<slug>`.
- Exclure URL de compte ou d'acquisition, hôte externe, userinfo, port
  détourné, fragment, paramètres contenant des jetons, chemins ambigus
  et traversées. Le seul paramètre toléré par compatibilité est
  `ref=library` pour une URL héritée `/ebooks/`.
- Appliquer la validation lors de l'extraction HTML **et** de la
  projection d'une ancienne entrée persistée. Stocker uniquement
  l'attestation `public_url_evidence=numilog.library_title_link`
  lorsqu'un lien est accepté.
- Aucun ajout d'HTTP, aucune altération du lien privé de téléchargement,
  de l'identité propriétaire, ni des métadonnées d'âge.

Tests hors réseau : `tests/test_public_book_link_guard_p2.py`.

**Suite distincte, non livrée** : capture réelle expurgée de « Mes
Livres », vérifier la redirection vers la bonne édition publique,
comparer l'EAN de l'EPUB puis qualifier ses données HTML/JSON-LD. Cette
qualification est nécessaire avant tout enrichissement de bibliothèque
ou filtrage Jeunesse depuis les pages commerciales.


## Connexion déclarée perdue après le démarrage (#1884)

Depuis 0.1.19, le log applicatif expose `TRACE numilog session` avec un
booléen `connected` et une raison fixe : connexion commencée/réussie/échouée,
fermeture explicite, opération refusée car déjà déconnectée, redirection hors
bibliothèque, structure de bibliothèque invalide ou panne transport. Ces
événements existent même sans collecteur Diagnostics ; aucune URL, identité,
credential, donnée HTML, cookie ou exception brute n'est journalisée.
`library_redirect` décrit la décision du provider de déconnecter après une
redirection ; ce code ne prouve pas à lui seul une expiration côté fournisseur.
Une erreur de structure ou de transport ne change pas l'état connecté.

Le log utilisateur du 16 octobre confirme une connexion initiale réussie,
mais sa tâche de téléchargement n'expose pas la cause de l'échec. Les notices
en cache ne prouvent pas une connexion active. Cette instrumentation ne
prétend pas corriger une cause amont non observée : aucune reconnexion,
requête supplémentaire ou modification des gardes d'acquisition.


## Parseur de recherche publique hors réseau (#1886, 0.1.20)

`cifereca_numilog_public.parse_public_search(html, source_url)` lit une page
publique du DOM attesté par la capture Jefferson du 16 octobre. Il retourne
`PublicSearchPage` : notices `SearchResult`, total annoncé, index de page,
prochain lien GET, facettes et options de tri observées. Le booléen `complete`
ne vaut vrai que si la première page couvre à elle seule le total annoncé,
sans lien suivant. Aucun résultat vide n'est fabriqué à partir d'une page
inconnue, d'une grille absente ou d'un total contradictoire.

Les notices portent `numilog:public:ID`, `source_credential=__public__`,
`source_collection=public_catalog`, `owned=False`, `downloadable=False` et
aucune URL de téléchargement. Ce namespace est refusé par le chemin privé
d'acquisition. Les cartes homonymes PDF et EPUB conservent leurs IDs distincts.
Le prix alimente `SearchResult.price` (chaîne décimale) et `.currency`, ainsi
que `metadata.price` (centimes, parties brutes, portée offre de carte,
sélecteurs). Un prix complètement absent reste inconnu ; un prix fragmentaire,
ambigu ou de devise non qualifiée provoque une erreur publique fixe.
Les champs présents ont leur preuve HTML et leur URL source dans
`metadata.field_evidence`. Aucun ISBN n'est déduit des couvertures.

Le parseur borne HTML, structure, cartes, textes et navigation. Les liens de
pagination doivent garder la même requête et les mêmes filtres ; la résolution
des liens produit relatifs est précédée d'une validation de leur forme brute.
Aucune URL privée/signée, origine externe ou traversée arbitraire ne passe
comme URL produit/navigation. Les couvertures non qualifiées sont omises avec
un avertissement fixe. Les champs optionnels absents restent inconnus.

**Étape transport public explicite** : `NumilogSession.search(query, scope='catalog',
field='all', page=n)` utilise désormais un **nouveau client HTTP anonyme** à
chaque appel, sans cookies du compte, sur le chemin HTTPS public `brSimple`
attesté par la fixture. Il refuse les redirections, MIME inattendus, réponses
supérieures à 4 MiB, 403/429 et HTML dont la structure diffère ; jamais de
repli sur un endpoint privé. `search_public()` est aussi exposée comme
méthode explicite de session. Tout autre `field` public est refusé : les modes
auteur/ISBN filtrés ne sont pas encore prouvés. Le scope normal `all/digital`
continue temporairement de chercher dans **Mes achats** pour ne pas changer
silencieusement les workflows privés avant intégration du moteur fédéré ; le
scope `catalog` ne donne pas de droit d'achat ou de téléchargement.

**Limite de preuve** : transport live anonyme, conditions d'accès, robots/CGU,
réponses filtrées, vrais zéros, deuxième page et DOM fiche produit restent à
qualifier sur des observations publiques autorisées. Les tests hors réseau
n'attestent pas un succès HTTP chez Numilog. Le déploiement en recherche
fédérée par défaut et ses capacités publiques restent à coordonner côté Core.
Les cas dérivés de tests ne sont pas des captures amont réelles.
La documentation de référence demeure `docs/NUMILOG-PUBLIC-SEARCH-FIXTURE.md`.

## Fournisseur public séparé pour la recherche fédérée (#1886, 0.1.22)

Le même package propose deux providers indépendants :

- `provider.numilog` / ID `numilog` : **Mes achats**, configuration avec
  identifiants privés, récupération des droits attestés et acquisition ciblée.
  Sa méthode `search()` historique demeure inchangée.
- `provider.numilog.catalog` / ID `numilog-catalog` : **Catalogue public**,
  `singleton_public` sans identifiants ni compte fictif, uniquement
  `catalog.search`. Il retourne les notices du parseur public en préservant
  le prix, la provenance et `owned=False`, `downloadable=False`,
  `download_url=None`. Aucune méthode d'acquisition n'est exposée.

**Activation explicite** : le plugin public a `enabled_by_default=false`.
Après qualification des règles d'accès et activation dans le gestionnaire de
plugins, le Core peut connecter cette source anonyme sans créer de compte
Numilog ; elle devient alors une source distincte de la recherche fédérée.
Seul un clic de recherche provoque un GET HTTPS public, jamais la connexion
ou l'affichage du catalogue. Les champs serveur auteur/ISBN restent
non qualifiés : ce fournisseur annonce seulement `field=all` et les scopes
`all/digital` ; il ne prétend pas être exhaustif sur les pages suivantes.
La source privée et ses éventuelles données en cache ne servent **jamais** de
repli à une recherche publique indisponible.

**Limite de preuve** : aucune réponse HTTP anonyme réussie ni qualification
robots/CGU n'a été établie à ce stade. La mise en service réelle reste
conditionnée à cette vérification ; les tests sont déterministes et hors réseau.

## Propriétaire stable pendant une livraison (#1905, 0.1.24)

Le compte lié au broker public `identities` est capturé avant la lecture
privée de livraison. Son propriétaire doit rester identique après cette
lecture, avant chaque saut HTTP, après lecture du corps et avant publication
EPUB ou import ACSM. Une liaison disparue, ambiguë, réattribuée ou devenue
illisible arrête le parcours avec un message fixe, sans identité ni erreur
brute dans les diagnostics. L'import reçoit toujours l'identité capturée.
Les appels EPUB historiques sans broker conservent leur contrat ; le package
installé exige les brokers `identities` et `acquisition`, et aucun ACSM ne
peut être consommé sans propriétaire résolu.

Un changement pendant un appel au broker déjà démarré ne peut pas annuler
ce fulfillment : le contrôle au retour refuse de revendiquer un succès et
conserve l'artefact éventuel du propriétaire initial. Aucune suppression ou
reprise automatique. Cette garde ne fournit ni lease transactionnelle Core,
ni ledger d'achat/variante, ni autorisation de sauvegarde automatique.

## Enrichissement explicite des achats par carte publique (#1072, 0.1.23)

`NumilogSession.enrich_owned_public_listing(owned_item)` est une opération
**opt-in** : elle vérifie l'identité du compte et retrouve une ligne
`MesLivres` **fraîche** sans charger les preuves signées de livraison. Une
unique recherche anonyme `brSimple` sur le titre de la ligne sert à **trouver
des candidats seulement** ; l'édition n'est confirmée que si l'URL canonique
du produit figure explicitement dans la carte possédée **et correspond
exactement** à l'URL d'un résultat public dont l'ID produit, le namespace,
le statut non possédé et la provenance HTML concordent. Un titre identique,
un ID `lid`, une couverture ou un ISBN papier ne suffisent jamais.

La méthode retourne une nouvelle notice `SearchResult` reconstruite
depuis la ligne achetée ; elle ne modifie pas le résultat fourni par l'appelant.
Elle peut compléter auteur, éditeur, date, résumé-extrait, couverture et
collection commerciale s'ils sont attestés par la carte publique ; le prix
courant reste exclusivement `metadata.public_offer_price` avec preuve.
**Ce prix n'est jamais déclaré prix d'achat historique.** Ni format possédé,
ni ISBN numérique, ni DRM, ni âge n'est déduit de cette carte multi-formats.
La provenance est consignée sous `metadata.public_enrichment`.

L'opération n'est **pas** appelée automatiquement par `search()`,
`library()`, `holdings()`, la synchronisation ou le rendu des listes :
aucun N+1, aucune acquisition ni récupération ACSM. Elle se limite à
**une page** publique : absence de correspondance ≠ absence du livre au
catalogue complet. Lorsque deux cartes du même achat/variante se répètent
sur une page ou entre pages, le rapprochement est refusé **avant tout GET
public**, même si l'inventaire ordinaire déduplique ces cartes. La preuve
\`lid\` reste distincte de l'ID du produit commercial. 403/429, erreur réseau et structure changée sont
des erreurs publiques typées, sans repli sur le client authentifié.

Reste à livrer : action UI explicite, fiche produit HTML réellement
qualifiée, ISBN numérique/PDF distingué, cache et politique TTL vérifiés,
puis recette réelle autorisée. Ce jalon ne clôture pas #1072.


## Fiches commerciales publiques explicites (#1886 / #1072, 0.1.25)

La consultation de **fiches produit publiques** reste expérimentale, désactivée
dans les parcours automatiques et distincte de `Mes achats` :

- `cifereca_numilog_public_detail.parse_public_detail_jsonld(html, url)`
  extrait hors réseau un seul objet JSON-LD `Book` dont l'URL correspond
  exactement à l'URL canonique de produit Numilog. Titres, auteurs, éditeur,
  description, langue, date et ISBN sont copiés seulement si présents et
  qualifiés. Une page sans objet univoque échoue explicitement.
- `fetch_public_detail(url)` effectue **un GET HTTPS anonyme à la demande** :
  nouveau client, aucun cookie/broker privé, aucune redirection, MIME HTML,
  timeout et limite 1 Mio, codes publics expurgés (401/403, 429, 5xx, DOM).
- `NumilogSession.public_product_detail(url)` donne accès au résultat
  structuré **sur demande explicite** dans le provider privé, sans
  enrichissement automatique d'achat.
- `NumilogPublicSession.enrich_public_detail(item)` valide avant tout GET
  que le résultat provient du catalogue public `numilog-catalog`,
  `__public__`, `public_catalog`, sans droits de possession ni URL
  d'acquisition, puis fusionne les seuls champs manquants dans une nouvelle
  notice. Les divergences entre carte et fiche sont consignées ; la carte
  d'origine reste intacte.

Le JSON-LD `isbn` est conservé sous **`isbn_edition_unspecified`** :
ni l'EAN de l'EPUB, ni celui du PDF, ni une édition effectivement achetée ne
peuvent être déduits de ce champ. Offres, prix privés, liens d'acquisition,
DRM et droits de possession ne sont jamais projetés. Aucune modification du
chemin de synchronisation ou téléchargement, aucun `GET` par résultat de
recherche, aucun hook UI automatique. Le provider public reste
`enabled_by_default=false`.

**Limites** : les tests JSON-LD et HTTP sont synthétiques et déterministes ;
les pages produit réelles et leurs JSON-LD, la seconde page commerciale et
les conditions d'accès live restent à qualifier. Le contrat Core `holdings`
et l'action UI ne sont pas branchés dans cet incrément. Il n'existe donc
aucune garantie d'extraction de champs JSON-LD présents sur les pages réelles.


## Achat Numilog : fiche commerciale détaillée sur demande (#1072, 0.1.26)

`NumilogSession.enrich_owned_public_detail(owned_item)` est une **nouvelle
action opt-in Python**, non déclenchée par `library`, `search`, `holdings`,
la synchronisation, le démarrage ou l'interface. Le point d'entrée réutilise
la vérification d'appartenance du compte et la lecture de l'inventaire
`MesLivres` frais de `enrich_owned_public_listing` : doublons, compte
différent, achat disparu et URL non attestée sont refusés. Le lien public
exact de la ligne achetée doit être confirmé par une **carte produit publique**
sur la première page de recherche avant une seule requête additionnelle
anonyme vers la fiche `/ID/slug.ebook`.

L'objet JSON-LD `Book` est analysé sans faire appel au client privé.
`merge_attested_owned_public_detail` exige la même URL canonique et
un titre cohérent ; un titre contradictoire bloque l'enrichissement. Seuls
les champs bibliographiques **manquants** sont projetés sur un **nouveau**
`SearchResult`, avec preuves sous `metadata.public_detail_enrichment` ;
les désaccords avec la carte publique sont conservés comme avertissements,
et aucune métadonnée ou droit transmis par l'appelant non fiable n'est repris.
Un ISBN non rattaché à un format reste
`public_detail_enrichment.isbn_edition_unspecified` : **aucune preuve
d'ISBN numérique acheté**, d'EPUB/PDF réellement détenu, de DRM, de droit
de fulfillment, de fichier ou de prix historique n'est inférée. Les champs
de droits et les URL de téléchargement sont inchangés.

La fiche HTML live, ses JSON-LD et la correspondance d'édition numérique
restent à qualifier avec captures autorisées. La méthode n'est pas encore
raccordée au contrat `holdings` ni à une action UI ; le catalogue public
reste désactivé par défaut. Les tests sont **offline et synthétiques**,
sans achat ou requête réelle. Le P0 #1072 reste ouvert après cette tranche.


## Notice du catalogue commercial dans le détail Core (#1886, 0.1.27)

Le singleton `numilog-catalog` déclare maintenant **`catalog.detail`** /
`ProviderCapabilities.holdings=True`. Core appelle automatiquement
`holdings(item)` lorsqu'une notice issue d'une recherche est sélectionnée :
cette méthode ne fait **aucun appel réseau** et restitue uniquement les
champs attestés **déjà présents dans le `SearchResult`** (titre, auteurs,
date, couverture). Elle affiche le statut conservateur
« Catalogue Numilog — achat non attesté », avec
`availability_status=unknown` et `holdings=[]`. Aucun prêt ni achat ne
peut être inféré de cette vue ; ni `acquisition.download` ni droits ne sont
ajoutés au manifeste de ce singleton.

Avant affichage, `validate_public_detail_item` contrôle la provenance de
la notice, le produit Numilog, le namespace `numilog:public`, la requête
publique et les attributs `owned=False`, `downloadable=False`,
`download_url=None`. Toute notice étrangère ou non qualifiée est rejetée
avec un code public fixe. Une fiche enrichie explicitement auparavant
conserve ses métadonnées et le même statut d'achat non attesté.

**Distinct de** `enrich_public_detail(item)` et
`enrich_owned_public_detail(item)` : ces opérations restent **uniquement
à la demande**, sans intégration à la sélection, au démarrage, à la
synchronisation ou au téléchargement. Le provider public reste
`enabled_by_default=false` jusqu'à qualification réelle de son transport
et de ses conditions d'accès. Les tests sont hors réseau, notamment sur la
fixture Jefferson expurgée.


## Identifiants des offres commerciales typés et isolés (#1072, 0.1.28)

Les pages publiques Numilog distinguent explicitement l'**EAN papier** et
l'**EAN EPUB**, parfois `EAN EPUB SANS DRM` ; elles peuvent aussi annoncer
un `ISBN numérique` sans préciser le format. Constats publics au
17 octobre 2026 (consultation de la page, **pas une capture HTML** intégrée
aux tests) :

- [Blackbone — Coltan Song](https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook) :
  EAN papier `9782092591086`, EAN EPUB sans DRM `9782092591093`.
- [Le Piège de Sainte-Soline](https://www.numilog.com/1956345/le-piege-de-sainte-soline--megabassines--la-lutte-pour-l-eau.ebook) :
  ISBN papier `9782380354522` et ISBN numérique non typé
  `9782380354539`. Ce dernier **n'est pas présumé EPUB**.

`cifereca_numilog_public_identifiers.extract_public_identifiers(html, url)`
lit **hors réseau** uniquement des couples étiquette/ISBN-13 explicites,
visibles et à somme de contrôle correcte. Les rôles distincts
`paper`, `epub`, `pdf`, `digital_unspecified` ne sont ni déduits de la
couverture, ni des guides génériques DRM, ni d'un même titre. Un doublon
contradictoire échoue fermé ; les scripts/formulaires sont ignorés et la
lecture est bornée. La valeur de DRM éventuelle provient **exclusivement**
de l'étiquette de l'offre (ex. `EAN EPUB SANS DRM`) : jamais des conseils
généraux du site.

L'extraction est ajoutée **en option probante** à
`parse_public_detail_jsonld`, seulement après qualification du JSON-LD
`Book` sur l'URL exacte. Les valeurs sont gardées séparément dans
`public_identifiers` et `identifier_evidence`, avec
`owned_format_proven=False`, et sont revérifiées avant toute projection sur
une notice catalogue ou achetée. Un format de l'**offre publique** ne prouve
pas le format acheté par le compte : les informations restent sous
`metadata.public_detail.public_identifiers` ou
`metadata.public_detail_enrichment.public_identifiers`, **jamais** dans
`isbn_digital`, `format`, l'inventaire d'artefacts ou les droits.

La sélection de notice reste **sans GET supplémentaire** ; seul le parcours
explicite de fiche peut consulter ces valeurs. Le provider public reste
désactivé par défaut. Les tests HTML sont **synthétiques** et ne qualifient
pas encore la structure DOM réelle de Numilog : confirmation sur captures
expurgées avec consentement, ISBN EPUB/PDF multiples et protections
réelles reste nécessaire avant promotion de ces champs dans l'UI.


## Bornage des identifiants visibles d'une fiche (#1072 / #1886, 0.1.29)

La recherche d'EAN/ISBN HTML de `0.1.28` parcourait tous les textes
visibles dès qu'une page contenait « Détails ». C'était insuffisant :
un EAN inclus dans l'en-tête, un pied de page, un guide des formats ou une
suggestion pouvait être **faussement attribué au livre**.

`extract_public_identifiers` isole désormais les textes visibles **après
un seul `h1` produit**, en exigeant un marqueur « Détails » dans une
fenêtre bornée, et en séparant deux zones :
- la description du livre, limitée par le début du guide des formats ;
- le bloc « Détails », limité par les titres « Auteur(s) du livre »,
  « Avis clients », « Suggestions personnalisées », etc.

Le parseur ignore en outre `script/style/form/template/nav/header/footer/`
`aside/dialog/iframe`; il refuse les pages à titres `h1` multiples,
les marqueurs manquants et les blocs de taille excessive. Un ISBN
papetier/numérique explicitement écrit dans le résumé reste admissible
**uniquement avant le guide**, tandis que l'EAN EPUB explicitement étiqueté
dans « Informations ebook » reste admissible dans « Détails ».

Chaque preuve porte désormais
`observation_scope=bounded_product_body_or_details`, revalidé par les
deux chemins d'enrichissement avant propagation. Une ancienne preuve sans
cette portée ne peut pas être promue en ISBN de fichier possédé, ce qui
reste interdit indépendamment de la version.

**Qualification externe limitée** : le texte accessible des fiches
[Blackbone](https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook)
et [Sainte-Soline](https://www.numilog.com/1956345/le-piege-de-sainte-soline--megabassines--la-lutte-pour-l-eau.ebook)
montre les labels et leur ordre, mais le HTML brut n'a pas été obtenu dans
l'environnement de recette. Tous les nouveaux tests DOM sont **synthétiques** :
cette version améliore l'isolement et **ne constitue toujours pas une
homologation de scraping Numilog en production**. L'activation automatique
du singleton public reste désactivée. Aucune requête supplémentaire à la
sélection/synchronisation ni aucune preuve de possession n'est ajoutée.


## Stabilité et quotas du parseur JSON-LD (#1886, 0.1.31)

Le parseur de fiche produit impose un plafond de **128 Kio UTF-8 par
script JSON-LD**, en plus du plafond HTML de 1 Mio, du nombre maximal
de scripts et de la profondeur d'exploration. Le décompte est cumulatif,
linéaire sur des morceaux HTMLParser successifs : un flux fragmenté ne
provoque ni contrôle de taille quadratique ni dépassement par caractères
multioctets.

Les erreurs sont intentionnellement **typiées**. Les violations
`jsonld_too_large` et `too_many_jsonld_scripts` restent
distinctes de `invalid_html`, `invalid_jsonld` et
`missing_or_ambiguous_book`. Aucun fragment de HTML, URL d'acquisition
ou valeur de page privée n'est inclus dans les messages. La couche
HTTP publique continue de transformer les erreurs de structure en son
code générique expurgé pour la présentation utilisateur.

Cette correction ne modifie ni les droits de possession, ni la
consultation implicite de fiches, ni le statut de qualification du
scraping HTML réel. Tests entièrement hors réseau.

## Livraison refusée avec formulaire de connexion (#1884 / #2024, 0.1.30)

`login_form_detected` signifie seulement qu'un champ HTML de mot de passe a
été reçu à la place du fichier. Cela ne prouve ni expiration du cookie, ni
refus serveur précis. Le provider suspend la session et demande une reconnexion
explicite, sans retry ni modification des règles de cookies ou des origines.
`connected_initial` conserve le booléen initial de la tentative ; la transition
`delivery_login_form_detected` expose ensuite `connected=False`. Aucun cookie,
URL, HTML ou identifiant n'est exporté. Le code historique
`session_expired_confirmed` reste accepté pour la lecture d'anciennes traces.

Tests MockTransport : connexion réussie puis readBook 302 / HTML login 200,
absence de fichier, nouvelle tentative après reconnexion explicite, cookies
fictifs Domain/Path/Secure/expiration. HTTPX conserve SameSite mais ne dispose
pas du contexte de navigation d'un navigateur pour l'appliquer : ces tests
ne prouvent pas une politique navigateur ni la cause du refus Numilog réel.
La recette fournisseur du même achat demeure non vérifiée.

## Fiches publiques observées et wrapper WebForms (#1886 / #1072, 0.1.32)

Deux GET anonymes, sans compte ni cookie préexistant, ont reçu HTTP 200 le
17 octobre 2026. Les extraits sous `tests/fixtures/observed_public_*.html`
conservent les balises/attributs pertinents et métadonnées publiques ; résumés,
état ASP.NET, champs cachés, suivi et scripts hors JSON-LD sont retirés.

| Fiche | Observation et régression |
| --- | --- |
| [1171334](https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook) | JSON-LD `Product`, `additionalType=Book`, `sku=1171334`, canonical exact ; formulaire `aspnetForm` POST sur le chemin produit. EAN papier 9782092591086 et EPUB sans DRM 9782092591093 distincts. |
| [1956345](https://www.numilog.com/1956345/le-piege-de-sainte-soline--megabassines--la-lutte-pour-l-eau.ebook) | Même structure. Les ISBN inline du résumé contredisent l'EAN papier de Détails : refus `public_identifier_evidence_unverified`, aucun choix automatique de l'édition. |

Empreintes SHA-256 des réponses publiques complètes (non versionnées) :
`654d1387e198cee55f1e95947ceae0111bec6f0dc82021c78e50da443829e1f8`
et `a95acc980f0ad469fc1a7671159e4a875083154e8d8afcf0ed9432cdb33bf315`.
Le second extrait conserve les labels contradictoires ; ne pas le réduire
à une fixture positive en supprimant cette preuve.

L'acceptation Product exige le type Book explicite, un SKU textuel identique
à l'ID numérique canonique et une seule notice correspondante. Le parseur
d'identifiants admet seulement le wrapper WebForms observé (id, name,
méthode, action exacte, sans attribut doublé) ; les formulaires de connexion
ou d'autres produits restent exclus. Aucun formulaire n'est soumis. Un
wrapper canonique dupliqué ou des identifiants contradictoires échouent fermé.
La provenance JSON-LD distingue Product/Book d'un Book direct ; `gtin13`
n'est jamais transformé en ISBN numérique ni en droit de possession.

`robots.txt` a aussi répondu HTTP 200 : son groupe générique interdit
notamment les chemins de compte, commande et handlers, sans interdire ces
fiches. Cette observation ne qualifie ni toutes les règles d'accès/CGS,
ni la recherche paginée, ni l'association d'un achat authentifié à une fiche.
Le catalogue reste désactivé par défaut. Aucun accès privé, lien ACSM ou
acquisition n'est ajouté ; #1886/#1072 ne sont pas terminés par ce lot.

## Recherche publique : deux pages et zéro résultat observés (#1886, 0.1.33)

Le 17/10/2026, trois GET anonymes sous le User-Agent public du plugin ont
reçu HTTP 200, après vérification du groupe robots applicable :

- `brSimple`, `query=loup`, page initiale : 20 cartes, total annoncé 973 ;
- même requête, `GRID_PAGE=1` : 20 autres cartes, total 973, IDs disjoints ;
- une requête synthétique sans résultat : table connue et marqueur explicite
  `GridView1_ctl01_lblAucunLivre`, sans compteur numérique et sans pagination.

La page suivante publie ses liens de retour à la première page **sans**
`GRID_PAGE`. Le parseur les normalise vers l'index 0, tout en vérifiant
l'origine et tous les autres paramètres ; il ne modifie pas la requête.
Un zéro sans compteur n'est accepté qu'avec l'unique marqueur observé, ses
classes et son texte exacts, sans carte ni pager. Page inconnue, message de
maintenance, compteur contradictoire ou structure ambiguë restent refusés.

Les fixtures `observed_search-*.html` contiennent **une carte par page
positive**, les compteurs et pagers observés, et le marqueur vide. Elles
sont des extraits DOM réduits, pas des réponses complètes ; les résumés,
états ASP.NET, scripts, liens reader/streaming et éléments de compte sont
retirés. SHA-256 des trois réponses complètes non versionnées :

- `5768b56acbc1e0565e1aa235b8f57290e887d76c371bf97b25e085773ae97d1f`
- `e2927ee9d1fdfd6a421f76b1707b27031c4ebed27233e5c8860942d1405c3ade`
- `e130eaced6d2705d9e453119209e0fe20ab392494a185397631818317cda8351`

Ces observations ne prouvent ni une recherche serveur par champ dédié
(auteur/ISBN/etc.), ni toutes les facettes, ni les modalités d'accès à long
terme. Le catalogue demeure à activation explicite. Aucune possession,
acquisition, page de compte ni requête supplémentaire implicite n'est ajoutée.


## 0.1.34 — déclarations HTML publiques observées (#1886 / #1072)

L'enrichissement explicite conserve maintenant `public_html_metadata` dans la
preuve de détail public et dans l'enrichissement d'un achat déjà rapproché.
Les seules valeurs validées alimentent aussi le namespace générique
`public_product_metadata` du Core (#2042), qui les présente séparément comme
déclarations commerciales, copiables sans recherche typée sur l'édition.
Aucune requête supplémentaire : même réponse bornée de fiche, jamais au rendu.

| Champ de provenance | Source observée dans la fiche | Interprétation |
| --- | --- | --- |
| `brand` | Bloc Détails, `LblPublisher=Marque`, `rptEditeurs…HLEditeurCol` | Marque affichée, sans conversion automatique en éditeur |
| `publication_date` | `lblDateParution` | Date ISO réelle du produit commercial |
| `pagination_label` | `lblNbrePage` | Texte publié ; édition/support de pagination non prouvés |
| `language_label` | `lblLanguage` | Libellé publié, aucune normalisation supposée |
| `collection` | `lblCollectionLivre` | `n.c` reste absent ; aucun titre/série inféré |
| `selected_format_label` | `BuySelectedFormat` | Format commercial sélectionné, pas liste exhaustive ni format acheté |

Chaque valeur conserve URL canonique, identifiant exact de balise, source HTML
et `edition_proven=False`, `rights_proven=False`. JSON-LD et valeurs existantes
restent séparés : aucune contradiction effacée, aucun droit ni champ `format`
de l'achat modifié. Le wrapper ASP.NET canonique, un h1 produit unique concordant
et le bloc Détails qualifient la zone. Contextes masqués, autres formulaires,
navigation et doublons de preuve ne fournissent pas de valeur. L'absence de
champ reste l'absence, jamais un défaut inventé.

Les fixtures `observed_metadata_blackbone.html` et
`observed_metadata_sainte-soline.html` combinent les extraits publics déjà
qualifiés avec le bloc Détails et le libellé de format de **ces mêmes réponses
anonymes du 17/10/2026** (empreintes HTTP originales documentées en 0.1.32).
Aucune description intégrale, état ASP.NET, cookie ou donnée de compte.
Confrontation hors réseau aux réponses complètes : mêmes cinq valeurs pour
Blackbone ; 146 pages / Massot éditions / 2026-10-01 pour Sainte-Soline.
Le conflit d'EAN réel de cette seconde fiche reste bloquant pour l'enrichissement
complet. Les deux fixtures ne prouvent aucune possession authentifiée.

Qualification complémentaire le 18/10 : GET anonyme du formulaire officiel
`/Pages/Recherche/RechercheAvancee.aspx` autorisé par robots générique, HTTP 200.
Il expose Titre, Auteur, EAN, format, rubrique et éditeur, mais utilise une
soumission ASP.NET cross-page. La soumission de recherche anonyme ciblée a reçu
HTTP 404 ; aucune capacité serveur dédiée auteur/ISBN n'est donc annoncée.
Aucun endpoint alternatif deviné, aucune utilisation du compte en repli.
Le catalogue reste désactivé par défaut ; #1886 reste ouvert pour les recherches
par attribut, facettes utilisables et qualification d'accès durable.


## 0.1.35 — budget et cache du catalogue public (#1886)

Le singleton public possède un cache **mémoire propre à sa session**, maximum
16 recherches/détails parsés, TTL 60 secondes. Ni HTML brut, compte, cookie,
ligne privée ni fichier ne sont conservés. Une copie défensive des résultats
empêche qu'un enrichissement ou consommateur altère le prochain résultat.
Connexion/reconnexion/fermeture invalident les entrées ; une réponse réseau
terminée après invalidation ne peut pas repeupler le cache. Aucun mutex retenu
pendant HTTP : fermer/invalider ne bloque pas sur une requête en cours.

Les nouveaux GET ont un budget de deux appels en rafale (recherche puis détail
explicite), puis un appel par seconde. Une requête simultanée en cours, budget
épuisé ou pause produit une erreur publique fixe, jamais un résultat vide,
une attente GTK ou une relance automatique. Un 429 public impose 60 secondes
de pause pour les nouveaux GET de cette même session ; reconnexion ne remet
pas à zéro cette pause ni le budget. Le contenu encore valide en cache reste
lisible sans réseau. 403, erreurs et résultats expirés ne sont pas servis
comme succès. Aucun mécanisme de contournement ni source authentifiée en repli.

Le provider privé continue d'utiliser ses requêtes publiques explicitement
scopées séparément ; ce cache ne contient pas d'achats ni de droits et n'est
pas partagé entre comptes ou sessions. Le TTL ne prouve pas la disponibilité
courante, une édition achetée ou un droit. Le catalogue reste opt-in ; les
capacités serveur auteur/ISBN ne sont pas ajoutées.

## 0.1.36 — respecter Retry-After public (#1886)

Un HTTP 429 de recherche ou de détail conserve uniquement le délai numérique
`Retry-After` (secondes ou date HTTP), sans corps ni en-tête brut. Le cache
public bloque les nouveaux GET pendant **au moins 60 secondes et jusqu'au délai
demandé par le serveur** s'il est plus long. Une valeur absente, invalide ou une
date passée conserve la pause minimale ; reconnexion/invalidation ne la réduit
pas. Aucun sleep, retry automatique, cookie de compte ou repli authentifié.
Les entrées en cache encore valides restent lisibles sans réseau. Les tests
HTTP synthétiques ne constituent pas une nouvelle qualification d'accès live.

## Diagnostic de téléchargement : preuves expurgées (#1884 / #2024, 0.1.37)

Chaque événement `delivery_http` de l'action explicite Numilog publie maintenant
`cookie_header_present: true/false` **uniquement comme indicateur de présence**
d'un en-tête HTTP `Cookie` sur la requête effectivement émise. Cela ne prouve
ni que le cookie est un cookie d'authentification, ni sa validité côté serveur.
Jamais de nom, valeur, domaine, chemin, nombre de cookies, URL ni jeton.
La destination `/Login.Aspx` ou `/Login.Aspx/AuthUser` sur l'hôte Numilog exact
est projetée en catégorie `login_page` ; les autres chemins restent `other`.
Les traces d'une réponse 302 sont toujours celles du saut courant : la destination
n'est connue que par la catégorie du saut HTTP suivant s'il a réellement lieu.
La combinaison `connected_initial=true`, présence/absence de Cookie et
`login_form_detected` peut orienter le diagnostic, **pas prouver une expiration**.
Ces traces ne modifient ni les règles de redirection, ni l'authentification, ni
les téléchargements. Validation live toujours nécessaire pour #2024.

## Referer du premier saut readBook (#2024, 0.1.38)

Lors du GET ciblé `/Pages/Compte/readBook.ashx` sur l'origine **exacte**
de la bibliothèque configurée, la première requête utilise désormais
`/Pages/Compte/MesLivres.aspx` (sans query) comme `Referer`.
Ce comportement reproduit la navigation normale depuis la liste des
livres, au lieu de présenter artificiellement l'accueil comme source.
Pour les autres chemins, autres origines et **tous les sauts suivants**
(HTML et ACSM signés), le `Referer` reste la racine publique du site.
Le `lid` privé, les paramètres `auth`, l'URL de fulfillment et le
contenu de réponse ne sont jamais transmis en `Referer` ni en diagnostic.
Aucun nouveau GET, retry, cookie, connexion ou droit n'est ajouté.

Cela corrige un écart client/navigateur plausible, **pas** une preuve que
Numilog rejette l'ancien en-tête : seule une recette authentifiée du
même achat permet de déclarer #2024 effectivement réparé.

## Formulaires de connexion intégrés aux pages Numilog (#1884/#2024, 0.1.39)

Un champ HTML `input[type=password]` peut appartenir au gabarit général
(les pages publiques Numilog incluent un bloc « Je me connecte »).
Un tel champ ne suffit donc pas à conclure que la session HTTP
authentifiée a expiré.

Désormais **tout HTML avec formulaire de connexion bloque toujours le
téléchargement**, y compris lorsqu'il contient un lien de livraison
plausible. La session est suspendue (`connected=False`) uniquement
lorsque la réponse vient de la route de connexion `/Login.Aspx` sur
l'**origine configurée exacte** ; sur les autres pages, le diagnostic
`login_form_detected` est conservé, mais l'état de session n'est pas
invalidé à tort. Aucun GET ACSM n'est effectué sur la seule base d'un
lien entouré d'un formulaire de connexion ; aucun retry ni reconnexion
automatique n'est introduit.

Cela ne prouve ni l'authentification acceptée par le serveur, ni un
téléchargement fonctionnel. La recette réelle #2024 reste indispensable.


## Cache catalogue public et résultats asynchrones périmés (#1886, 0.1.40)

Une recherche publique ou une fiche commencée avant fermeture ou reconnexion
ne peut plus restituer son ancienne réponse au Core après `clear()`.
L'invalidation de génération, déjà présente pour le cache, rejette désormais
**aussi la valeur retournée à l'appelant** avec le code fixe
`public_catalog_request_invalidated`. Aucun contenu de réponse, requête,
identité ou URL n'est journalisé par ce refus. Le verrou du cache n'est pas
tenu pendant HTTP ; une requête déjà en cours peut se terminer mais ne sera
ni mise en cache, ni présentée comme résultat de la nouvelle session.
Un appel ultérieur explicite réinterroge normalement le catalogue sous
le budget/cooldown existant, sans retry automatique ni réseau supplémentaire.

## Destination validée des redirections de livraison (#1884/#2024, 0.1.41)

La trace **existante** `delivery_http` du saut HTTP 301/302/303/307/308
indique dorénavant les catégories **fixes** `redirect_target_origin_kind`
et `redirect_target_kind` lorsque la valeur `Location` a été résolue et
validée par le garde-fou de destination du téléchargement. Exemples :
`numilog_site/login_page`, `acsm_fulfillment/acsm_fulfillment`,
`numilog_site/other`. Aucune URL de destination, query, fragment, nom
de cookie, ticket, contenu HTML ni compte n'est propagé dans la trace.

Une redirection refusée n'émet **aucune** catégorie de destination
(après 302 visible, code terminal `origin_rejected`) ; cette absence
ne doit pas être interprétée comme une réussite de navigation.
Le `login_page` d'une `Location` est seulement un indice de parcours
du serveur, **pas** une preuve d'expiration du cookie. L'ancienne
classification par saut HTTP suivant reste disponible ; le nombre
d'événements est inchangé. Aucun nouveau HTTP, retry, validation de
droit ou bascule de session n'est ajouté.

Les tests HTTPX fictifs couvrent les redirections vers page login avec
query secrète, page HTML ambiguë, ACSM signé, origine rejetée, et les
en-têtes Cookie fictifs de différents Domain/Path/Secure/SameSite.
Cela ne valide pas le parcours réel du compte Numilog : le P0 #2024
reste à qualifier avec diagnostic utilisateur expurgé.


## Numilog 0.1.42 — gabarit de connexion et confirmation d'achat strictement liée (#1884/#2024)

Le gabarit d'une page Numilog peut inclure un champ HTML mot de passe
pour se connecter, même si la page présente réellement la livraison
d'un achat. L'ancien comportement 0.1.39 refusait tout HTML comportant
ce widget, sans tenter la validation d'une confirmation d'achat.

**Exception très ciblée**, uniquement après une action explicite
`download_direct` d'un `readBook` de bibliothèque fraîche et sur la
route HTTPS canonique `/Pages/Commande/ConfirmationPaiement.aspx` de
**l'origine exacte** du compte configuré : si la page comporte un widget
de connexion, le provider peut maintenant poursuivre **uniquement** un
ACSM Adobe signé dont la liaison à l'achat est démontrée par toutes les
preuves structurelles déjà exigées :

- un seul tableau littéral `Links` et un seul candidat ADOBE valide
  vers l'origine `entrepot2r.numilog.com` ;
- un bouton `ModalDownload(ProductId, IndexExemplaire)` constant,
  unique et dans la même carte que le `readBook` de l'achat ciblé ;
- correspondance **exacte** des coordonnées `readBook` fraîches,
  sans présumer que son `lid` vaut le `ProductId` commercial ;
- absence de second ACSM signé et validation HTTPS/host/port/query
  par les gardes-fous actuels à chaque GET ;
- broker d'acquisition et identité autorisés inchangés, sans choix
  automatique de profil Adobe ni consommation opportuniste d'un lien.

Toute page `/Login.Aspx`, tout autre endpoint intermédiaire, toute
preuve d'édition/copie manquante ou contradictoire, toute structure
dynamique ou toute simple ancre ACSM **reste refusée** ; une page
d'authentification exacte continue à suspendre la session.
Aucun retry, cookie cloné du navigateur, nouvelle connexion, endpoint
ou téléchargement implicite n'est introduit. Les diagnostics conservent
des catégories et compteurs bornés, jamais l'HTML, un titre privé, un
`lid`, un cookie ou un URL ACSM signé.

**Limite de preuve :** les tests utilisent la réduction structurelle
déjà inspectée du parcours `ConfirmationPaiement` et des achats fictifs,
avec widget ajouté artificiellement. Ils ne prouvent pas que les
trois réponses HTML `other` observées chez l'utilisateur suivent cette
route, ni que le fournisseur accepte leur ACSM. Le P0 #2024 exige
toujours une vraie livraison réussie du même achat et un export
expurgé après installation du correctif.


## 0.1.43 — identifier le vrai saut ConfirmationPaiement sans exposer ref (#2024)

Le parcours navigateur rapporté pour le **même achat** passe par la route
`/Pages/Commande/ConfirmationPaiement.aspx?ref=<PRIVÉ>&disp=simple` et finit
par recevoir un ACSM. Le saut HTTP réel depuis `readBook` n'est pas encore
qualifié : aucun `ref`, URL de commande ni requête JS n'est reconstruit.

Sur les **événements de téléchargement existants**, le producteur Plugins
déclare `path_kind=confirmation_page` et
`redirect_target_kind=confirmation_page` **uniquement** pour la route HTTPS
exacte sur `www.numilog.com`. Il ajoute des booléens stricts
`confirmation_ref_present` et `confirmation_disp_simple` sur le GET
effectivement arrivé à la confirmation, et leur variante préfixée
`redirect_` sur le 302 **déjà autorisé** visant cette page. Une présence
`ref` signifie un paramètre unique et non vide ; `disp_simple` signifie
exactement un `disp=simple`. Plusieurs `ref` ou `disp` rendent leur
preuve correspondante `false`. Une URL non qualifiée n'expose aucun
de ces champs.

Aucun **contenu** de `ref`/`disp`, nom d'achat, URL signée, `Location`,
cookie, corps HTML ni titre n'entre dans les événements, logs ou
erreurs. La page n'est pas considérée comme appartenant au compte sur
ces seuls booléens : les contrôles d'identité, de copie exacte et
d'ACSM de 0.1.42 sont inchangés, sans requête supplémentaire.

La projection des diagnostics **Core** utilise une liste fermée :
elle doit explicitement reconnaître les nouveaux types/catégories, et
le booléen existant `cookie_header_present`, pour que l'export utilisateur
les affiche. Cette mise à jour Core est laissée au rôle Core (#1884) et
n'est **pas** contenue dans cette version du plugin.

Lorsque le gabarit de la vraie confirmation contient un widget
login **mais ne permet pas une liaison univoque**, l'événement
`html_selection` distingue désormais `html_delivery_link_missing`,
`html_delivery_ambiguous` ou `html_invalid` via les **codes publics
existants**, avec `html_kind=login` et un compteur borné de candidats.
Le code terminal reste `login_form_detected` et aucune session n'est
invalidée hors de la vraie page de connexion. Aucune URL signée n'est
consommée sur ces échecs : ces causes peuvent être observées **sans
extension du schéma Core pour les codes** (contrairement aux nouveaux
champs de route).

Tests HTTPX simulés : ancien `disp=1` et `disp=simple`, parcours
`readBook → 302 → ConfirmationPaiement → ACSM` avec et sans widget login,
refus sous product/copie/lid/modal ambigus ou absence de tableau de liens,
acheminement de la query d'origine non modifiée, et absence de fuites
à chaque événement. **Aucun téléchargement client live validé** :
le ticket P0 #2024 ne doit pas être clôturé sur ce lot.


## 0.1.43 — liaison obligatoire sur toute page de confirmation d'achat (#2024)

Une `ConfirmationPaiement.aspx` peut présenter plusieurs livres, offres ou
variantes. **Même sans formulaire de connexion**, un unique lien ACSM dans
une telle page ne prouve pas qu'il concerne l'édition de la carte sélectionnée.
Le provider exige désormais, sur la **route canonique** de confirmation,
le même tableau `Links` Adobe unique, le bouton `ModalDownload` constant
et la correspondance exacte du `readBook` issu de la bibliothèque fraîche.
Si l'une de ces preuves manque ou diverge, aucun ACSM n'est consommé.

Le repli historique d'une page *intermédiaire distincte* (qui contient
un seul lien signé admissible) reste inchangé. Tests fictifs couvrant un
lien unique `<a>`, un `window.location` JS et un `Links` non lié dans
la confirmation : tous refusés avant tout GET ACSM ; comparaison avec
un `Landing.aspx` ancien toujours fonctionnel. Il n'y a aucun changement
de compte, droit, endpoint ni suite d'appels réseau.


## Cookies HTTPS entre MesLivres, readBook et confirmation (#2024)

La présence de l'en-tête Cookie sur le GET `readBook.ashx` ne prouve
pas que ce même cookie est éligible sur
`/Pages/Commande/ConfirmationPaiement.aspx`. Les cookies déclarés
avec `Path=/Pages/Compte` ne transitent pas vers
`/Pages/Commande` ; ceux avec `Path=/` sont éligibles pour les deux.
Les tests HTTPX hors réseau couvrent également `Path=/Pages/Commande`
et `/Pages/Other`, avec un cookie `Secure` fictif et l'attribut
`SameSite=Lax`, en vérifiant **seulement** la présence booléenne au
niveau de la requête effectivement envoyée et du diagnostic expurgé.

HTTPX **n'émule pas le contexte de navigation SameSite du navigateur** :
ces tests documentent le filtrage Domain/Path/Secure, pas une preuve que
Numilog valide une session réelle. Aucun nom/valeur de cookie, query de
commande ou identifiant privé ne doit entrer dans un export.


## Rejet d'un HTML de livraison structurellement invalide (#2024)

Le parseur HTML de première classification peut refuser un DOM trop
profond ou contenant trop d'éléments, même si la **taille en octets**
est sous la limite de réponse. Ce cas renvoie maintenant un
`ProviderError` fixe, un `html_selection=html_invalid` et un
`completed=html_invalid` expurgés, à la place d'une exception
`ParseError` brute faussement classée `html_oversize`.

Aucun lien ACSM ni script n'est sélectionné dans cette situation.
Les tests hors réseau dépassent indépendamment la limite de
profondeur et la limite du nombre de nœuds. Aucun log du corps
HTML, d'URL de compte ou de texte d'exception amont n'est publié.


## Lien ConfirmationPaiement explicite dans la carte MesLivres (#2024)

Lors du **clic explicite** sur un achat, l'inventaire Numilog est relu
sous l'identité propriétaire courante. Si la carte de cette **édition
unique** comporte, en plus de son `readBook.ashx` déjà utilisé pour
l'identité du record, un lien HTML littéral unique vers
`https://www.numilog.com/Pages/Commande/ConfirmationPaiement.aspx`
avec une référence `ref` unique et non vide et un seul `disp=simple`,
le provider peut emprunter **ce href précis** comme premier saut.
Aucun `ref` n'est dérivé de `lid`, du titre, d'un ISBN, d'un cache
ou d'un lien trouvé dans une autre carte. Une route, un host, une
query ou un fragment inattendu n'est pas admis ; deux liens de
confirmation concurrents font échouer l'acquisition sans requête de
livraison. Une carte à plusieurs variantes ne permet pas d'attribuer
le même href à un exemplaire arbitraire.

Le `readBook` de la carte reste la preuve d'édition transmise au
validateur strict `Links` / `ModalDownload(ProductId, IndexExemplaire)`
de la page de confirmation. Si l'ACSM n'est pas rattaché au même
`readBook` frais, aucun fichier n'est consommé. Sur ce premier saut,
le `Referer` est `MesLivres.aspx` (sans URL de commande) ; les
suivants restent la racine du site. Le broker et l'identité Adobe ne
changent pas.

Les liens de confirmation sont **transitoires et ciblés**, extraits
seulement en `_rows(include_delivery=True)`, jamais en inventaire,
catalogue, `SearchResult`, cache, diagnostic ou suivi de droit.
Si la carte ne contient **pas** de lien explicite qualifié, le
fallback historique `readBook` continue sans changement.

Les tests offline couvrent les deux entrées (readBook 302 et href
direct) vers `disp=simple`, avec/sans widget login, liaison exacte,
mauvais achat, ancres concurrentes, variantes multiples, hosts,
paramètres et fragments invalides, cookies Domain/Path/Secure
synthétiques et absence de secret dans toute trace. La **structure
réelle de l'ancre cliquée** sur le compte Numilog et le succès ACSM
effectif demeurent non attestés ; aucune nouvelle route ou requête JS
n'est devinée.


## Cohérence de la référence de commande du formulaire de confirmation (#2024)

Si le formulaire HTML **englobant la carte de livraison** possède une
`action` vers la même route de confirmation et fournit sa propre clé
`ref`, cette référence opaque doit correspondre exactement à celle du
GET `ConfirmationPaiement.aspx` en cours. Une référence différente, vide
ou dupliquée provoque `html_delivery_ambiguous` **avant tout GET ACSM**,
même quand les `Links` Adobe, le `ModalDownload` et le `readBook`
semblent autrement correspondre. Les formulaires indépendants
(login/en-tête hors de la carte) ne définissent pas cette liaison.
Aucune comparaison de `ref` n'infère une équivalence avec `lid` ou
`ProductId` : les identifiants restent dans leurs espaces distincts.

La comparaison est en mémoire seulement, sans exposer l'un ou l'autre
`ref` dans la trace, l'erreur ou le Core. Tests offline avec formulaire
cohérent, référence contradictoire, vide/dupliquée et widget login
présent/absent : le refus n'envoie jamais l'ACSM.


## Numilog 0.1.45 — bornes sur la confirmation, sans effet sur les autres pages

La référence de commande du **GET réellement arrivé sur
`ConfirmationPaiement.aspx`** n'est interprétée qu'au moment où un
formulaire *englobant la carte de livraison* cible aussi cette route.
Le parsing des paramètres de **cette** URL est strict et limité à
32 champs : doublons, fragments de query incomplets et requêtes trop
complexes provoquent `html_delivery_ambiguous` avant toute récupération
d'ACSM. Les valeurs de référence ne sortent jamais de la comparaison
en mémoire. Une page intermédiaire distincte dont le formulaire pointe
simplement vers la confirmation n'a pas de référence GET à comparer et
conserve son traitement historique.

Un `href` direct de confirmation dans la carte MesLivres est en outre
limité à **4096 caractères** avant résolution et après `urljoin`. Une
référence anormalement longue est ignorée au profit du `readBook` de
l'édition fraîche ; aucun long URL de commande n'est transmis au
serveur ou sérialisé dans les objets du catalogue. Les limites ne
touchent pas au compte, aux droits, au courtage ADEPT ou à d'autres
endpoints Numilog.

Tests synthétiques : duplicats/paramètres invalides et surcharge
de champs côté GET, compatibilité des pages Landing, lien direct
de plusieurs milliers de caractères rejeté hors réseau. Aucun compte
réel n'a été utilisé ; #2024 exige toujours une livraison ACSM réelle.


## Numilog 0.1.46 — preuve de l'entrée réelle dans la livraison (#2024)

Un lien de confirmation directement issu de **la carte possédée
relue au clic** est maintenant déclaré dans les événements expurgés
`acquisition_delivery` avec `source=owned_card`. Si le fournisseur
répond à une requête `readBook` puis effectue une redirection HTTP 302,
la source reste `source=readbook`. Ce sont deux valeurs **déjà publiées
et admises par le Core** ; aucune nouvelle propriété Core et aucune
query privée ne sont requises pour les distinguer. Les phases
`delivery_http` permettent en plus de vérifier si le premier hop était
`confirmation_page` ou `readbook`.

Les deux entrées utilisent les **mêmes** exigences de possession,
readBook scoped frais, `Links` signé et bouton
`ModalDownload(ProductId, IndexExemplaire)`. Pour une confirmation
contenant un widget de connexion, le chemin `owned_card` a accès au
même validateur strict que `readbook` ; aucun formulaire ni login
d'une autre page n'est accepté. Les tests HTTPX simulent les deux
entrées avec et sans widget login, avec import ADEPT fictif unique,
sans HTTP supplémentaire ni URL de commande, cookie ou `ref`
dans les logs. Cette correction renseigne la provenance de la
navigation, **pas la validité d'une session réelle** ni le succès
d'acquisition sur le serveur Numilog.


## Numilog 0.1.47 — liaison réelle `m=dwld` / `m=stream` (#2024)

Une contre-épreuve du 18/10/2026 a identifié **le même `lid`** dans
deux ancres de **fonctions différentes** :

- `MesLivres.aspx` : `readBook.ashx?lid=<PRIVÉ>&m=dwld` — le lien
  de l'achat fraîchement possédé, utilisé au clic pour accéder à la
  livraison.
- `ConfirmationPaiement.aspx?ref=<PRIVÉ>&disp=simple` :
  `readBook.ashx?lid=<MÊME_LID_PRIVÉ>&m=stream` —
  l'ancre **« Lire en ligne (Streaming) »** dans la carte commerciale
  comportant le bouton `ModalDownload(ProductId, IndexExemplaire)`.

Le comparateur historique de `_confirmation_delivery` exigeait
l'égalité totale des paramètres GET, dont `m`, ce qui rejetait
systématiquement cette liaison `dwld != stream` comme ambiguë.
**Seule la liaison d'identité dans la confirmation canonique HTTPS
Numilog** tolère désormais le changement *directionnel*
`m=('dwld',)` sur le lien *MesLivres* → `m=('stream',)` sur l'ancre
de la carte de confirmation. Le `lid` numérique doit être identique
et **tous les autres paramètres**, clés, valeurs et répétitions
incluses, doivent correspondre exactement. Les URLs `readBook`
participant à cette preuve sont limitées à **4096 caractères** et
**32 paramètres**, et les fragments de query malformés (`&cle`
sans valeur) sont refusés au lieu d'être supprimés silencieusement.
L'ancienne égalité
stricte reste inchangée partout ailleurs (autres pages, catalogues,
livraisons directes, autres modes).

**Le lien `m=stream` n'est jamais téléchargé** : c'est seulement
un indice de rattachement à la carte. Le fichier est obtenu via
l'**unique** objet `Links` Adobe signé, après vérification du
`ModalDownload` constant (produit + index d'exemplaire), de la
carte unique, du `readBook` frais et de la référence `ref`
cohérente. Les alternatives LCP présentes dans `Links` ne sont
ni consommées ni traitées comme un second ACSM.

Tests HTTPX **sans compte réel** : les deux entrées `readBook 302`
et href direct d'achat, avec et sans widget login ; succès ACSM
fictif (broker appelé une fois) ; absence totale de GET
`m=stream` ; refus `lid` différent, mauvais mode, double mode,
paramètre supplémentaire, ordre des valeurs répétées différent,
streaming hors carte ou dupliqué, produit/copie/ref discordants,
route non canonique ou origin hostile. Les traces restent
expurgées (`lid`, `ref`, `orderid`, `resid`, `auth`, cookies,
URL ACSM, HTML et titres privés jamais exportés).

**Limite de preuve** : la présence et la position dans le DOM réel
du lien streaming ainsi que les droits/fichiers ADEPT de l'utilisateur
ne sont pas accessibles au test. Le ticket P0 #2024 reste ouvert
jusqu'à un ACSM véritablement récupéré puis validé par le broker
avec la version **réellement installée**, distincte du Core.


## Recette P0 #2024 — frontière `acsm_complete` et cache local (18/10/2026)

**Preuve fournisseur live :** un achat Numilog a réussi le 18/10/2026,
de `readBook 302` à `ConfirmationPaiement 200`, l'ACSM
`entrepot2r` puis `adept_fulfillment=acsm_complete`,
`completed status=ok`. Le plugin a donc remis au broker un ACSM
valide et reçu un **chemin de fichier existant** en retour. Ce succès
ne prouve pas que le reçu d'achat et le cache Bibliothèque/Recherches
aient été rapprochés ultérieurement.

Le contrat public du broker permet `status=complete` **ou**
`status=unchanged` et peut retourner un EPUB **ou PDF** dans un
sous-répertoire de l'identité et du fournisseur tel que
`<identité>/numilog/acsm-<empreinte>/<livre>.epub`. Le provider
**renvoie le fichier à son emplacement original sans le déplacer**
vers le nom provisoire `numilog-<hash>.epub`, ne déclare pas
`core_index_complete`, et ne scanne ni ne réindexe lui-même la
bibliothèque : seules les primitives officielles Core sont autorisées
à attribuer ce fichier à l'achat. Les tests synthétiques valident les
quatre combinaisons `complete/unchanged × epub/pdf`, sans publier le
chemin, le titre, la signature ACSM ou l'empreinte privée dans le flux
`NUMILOG`.

Les conflits de rattachement **ACSM-digest vs. identifiant d'achat**
et la conservation de `source_item_id` lors de `catalog_scan` sont
traités côté Core dans [#2112](https://github.com/pzia/cifereca/issues/2112).
L'invalidation du cache Recherche locale après indexation complète
est prise en charge par CoreUI dans
[#2114](https://github.com/pzia/cifereca/issues/2114).
La version Numilog 0.1.48 assure le transport ; cette validation
de contrat plugin ne modifie ni l'API, ni le package binaire, ni
le flux de droits et n'introduit aucun appel réseau.


## Numilog 0.1.50 — l’inventaire privé reste sur l’origine du compte

`connect()` et chaque page de `MesLivres.aspx` doivent désormais
provenir de **l’origine exacte** de la bibliothèque privée configurée.
Les origines supplémentaires autorisées pour le transport d’EPUB/ACSM
(`services.numilog.com`, `edenlivres.fr`, certains hôtes de stockage
et l’entrepôt ADEPT) **ne certifient jamais une possession**.

Le client vérifie **avant d’émettre le GET suivant** que la cible d’une
redirection 301/302/303/307/308 d’inventaire reste sur cette origine,
même si l’autre hôte est déjà présent dans l’allowlist réseau et expose
un chemin `/Pages/Compte/MesLivres.aspx` identique. Le parseur continue
à exiger la structure MesLivres et les liens de pagination sur la même
origine. Une cible rejetée ne provoque ni POST d’authentification
supplémentaire, ni GET externe, ni droit `owned`, ni acquisition.
Le blocage d’origine n’est **pas** présenté comme preuve d’expiration
de session.

Les redirects habituels `MesLivres → MesLivres?page=N` de la **même
origine** restent pris en charge ; la configuration HTTP de simulation
locale `127.0.0.1` conserve son origine sans promouvoir automatiquement
un domaine de production. La validation des origines de **livraison
ACSM**, distincte de l’inventaire, n’est pas élargie ni modifiée.

Tests exclusivement `httpx.MockTransport` : redirection de la première
page et d’une page paginée vers trois hôtes partenaires/CDN autorisés
(parcours bloqué avant GET), confirmation de connexion rejetée si
la redirection MesLivres change d’origine, simulation loopback et
pagination/redirection normale. Aucun cookie, mot de passe, identifiant
d’achat ou HTML privé ne rejoint les diagnostics.


## Catalogue public : navigation sur facettes attestées (0.1.52, #1886)

`NumilogPublicSession.browse_public_search(query)` expose un `PublicSearchPage`
complet : total public annoncé, notices commerciales, liens de page suivante,
facettes publiées (`fltrTheme`, `fltrPrice`, `fltrLang`, `fltrPub`) et options
de tri observées. `search()` utilisé par le Core reste inchangé (une liste
de notices). Les résultats sont `owned=False`, `downloadable=False`, sans
`download_url` ni inférence sur le prix historique ou les achats.

`follow_public_link(url)` suit exclusivement un lien de filtre/pagination
publié par une réponse `browse_public_search` ou `follow_public_link`
validée dans la même session. Autorisation valable 60 secondes, révoquée
à la déconnexion/reconnexion. URL construite par l'appelant, page >100,
paramètre non attesté et lien de fiche/achat sont refusés avant HTTP.
Le suivi reste anonyme, HTTPS, sans cookies ni redirections, avec le
même cache TTL/budget HTTP que la recherche simple. Pas de crawl automatique.

Il s'agit de navigation facettée distante sur les URL effectivement
publiées, pas d'une preuve d'exhaustivité ni de recherche serveur
par ISBN, auteur ou titre exact. Singleton toujours désactivé par défaut.
Action UI/Core pour exposer ces facettes et qualification HTTP live/robots/CGU
restent à faire. Les tests sont déterministes et hors réseau.


## Numilog 0.1.53 — résumé complet sourcé des achats (#1072)

L'action **explicite** `enrich_owned_public_detail(item)` conserve désormais
tous les champs du `Book` JSON-LD public dont l'URL et le titre sont
identiques à l'édition achetée et à sa carte commerciale attestée, sous
`metadata.public_detail_enrichment.public_jsonld.fields`, accompagnés des
`field_evidence` par champ et du statut `edition_proven=false`.
Ces informations proviennent de la **fiche commerciale** et ne
prouvent toujours ni le format acheté, ni un fichier, ni un droit DRM.

Si le résumé complet de la fiche **prolonge réellement l'extrait**
présent dans la carte HTML, et que cet extrait est accompagné de
sa preuve `p.list-group-item-text` pour la même recherche, le
`SearchResult.description` prend le résumé complet. L'extrait initial
reste conservé sous `replaced_listing_excerpt`. Si le texte diffère
ou que la preuve manque, le texte précédent est conservé et les deux
valeurs demeurent consultables avec le conflit explicite.
Les valeurs JSON-LD sont revalidées avant toute fusion (taille,
caractères de contrôle, dates, auteurs, EAN) ; refus par code fixe
sans répercuter la donnée reçue.

Aucune nouvelle requête HTTP, lecture automatique, acquisition,
association de titre approximative, projection d'ISBN papier en EPUB,
ni changement Core/UI. Tests offline : résumés complets, divergences,
métadonnées publiques, deux homonymes PDF/EPUB, preuves manquantes
et données falsifiées. La qualification des achats réels reste ouverte.


## 0.1.54 — Offre commerciale et couverture JSON-LD qualifiées (#1886)

`parse_public_detail_jsonld` conserve désormais la seule offre Product/Book
de type `Offer` avec prix **décimal EUR affiché**, disponibilité Schema.org
explicitement publiée (`in_stock`, `out_of_stock`, `pre_order`)
et URL de couverture canonique `couverture.numilog.com`, si elles sont
strictement validées. Ces attributs restent isolés dans
`public_commercial.fields` et `field_evidence` avec provenance exacte
`Product.offers.*` / `Product.image`, source de fiche et indicateurs
`edition_proven=false` / `rights_proven=false`. Une image dont le nom
contient l'EAN papier n'établit **pas** l'EAN EPUB.

Le provider catalogue et l'enrichissement explicite des achats conservent
cette section dans les métadonnées de la **fiche publique** seulement.
Les prix déjà présents dans les résultats de recherche et les prix
historiques d'achat ne changent pas ; aucune disponibilité marchande
n'est interprétée comme un droit à télécharger. Aucun URL d'offre
(même présent dans le JSON-LD), vendeur, jeton ou lien d'acquisition
n'est repris. Une offre multiple/ambiguë ou non qualifiée ne fournit
aucun prix. Cette extraction n'ajoute aucun appel HTTP ni N+1.

Preuves : capture publique expurgée Blackbone du 17 octobre 2026 et
tests déterministes d'offres invalides, provenance falsifiée, couverture
externe, URLs signées et séparation papier/EPUB. Disponibilité live,
conditions robots/CGU et moteur multi-attributs serveur restent à qualifier.



## 0.1.58 — preuve de concordance achat / fiche publique (#1072)

Le rapprochement explicite entre une ligne fraîche **Mes Livres** et une
carte commerciale du même identifiant/URL Numilog exige désormais aussi un
titre identique après normalisation Unicode NFKC, espaces et casse. Une
fiche publique dont le titre diffère provoque
`public_edition_title_conflict` **avant toute projection** d'auteur,
éditeur, résumé, date, couverture, collection ou prix. Une édition homonyme
située sous une autre URL conserve sa séparation, même si son titre est
identique. Le titre privé provenant du compte ne peut jamais être remplacé
par celui d'une fiche publique.

Les sélecteurs de provenance de chacun de ces champs sont exacts, et pas
simplement un champ `kind=observed_public_html` accompagné d'une URL.
Le prix commercial doit être cohérent avec les morceaux de prix observés,
sa devise EUR, sa valeur numérique et ses sélecteurs exacts. La projection
conserve uniquement montant, centimes, devise et portée de l'offre
**publique**, jamais les valeurs brutes d'une carte non qualifiée, et ne
modifie ni prix historique d'achat, ni format, ni propriété, ni droit
d'acquisition.

Cette vérification hors réseau ne remplace **pas** la preuve manquante
d'association authentifiée entre une édition EPUB/PDF achetée et la
référence numérique de la fiche. Tests entièrement synthétiques utilisant
la capture publique expurgée Jefferson ; aucune nouvelle requête, aucun
ACSM ni récupération de contenu privé. La version 0.1.58 suit la 0.1.56 (EAN canonique) et la 0.1.57
(affinage local), conservées lors de l'intégration CI/CD.
## 0.1.57 — affinage bibliographique local sur une page Numilog (#1886)

La session anonyme `NumilogPublicSession.refine_loaded_page(page, **filters)`
affine **uniquement une page déjà renvoyée par** `browse_public_search`
ou `follow_public_link` et n'émet **aucune nouvelle requête**. Champs
acceptés : `title`, `author` (y compris co-auteurs), `publisher`,
`year` (année entière) et `format_label` (format commercial affiché).
Chaque correspondance exige un champ et son sélecteur de provenance
`observed_public_html` de la même recherche ; les champs absents ou
non attestés sont comptés en `missing_evidence_count`, pas en preuve
d'absence du catalogue. Comparaison locale insensible aux accents,
casse et ponctuation ; aucun ISBN, âge ni recherche native auteur
**serveur** n'est inventé.

Le résultat `PublicPageRefinement` distingue
`matching_count` de `public_unfiltered_total`, indique
`scope=loaded_public_page_only`, `loaded_count`, la page interrogée,
la présence de pages non parcourues et le nombre de fiches incertaines.
Ce n'est **pas un total filtré serveur**, ni un filtrage global. Les
résultats demeurent `owned=False`, `downloadable=False`, et sans URL
d'acquisition. La méthode est **optionnelle** ; la recherche Core
conserve `field=all` et ne présente pas cette fonction comme une
facette distante. Tests hors réseau sur cartes publiques expurgées,
limites de champs, provenance et identités, sans cookie/ACSM.


## Numilog 0.1.56 — EAN dans la fiche canonique uniquement (#1072)

Les identifiants publics typés `paper`, `epub`, `pdf` et
`digital_unspecified` ne sont désormais retenus par
`parse_public_detail_jsonld` que si les étiquettes appartiennent
au **formulaire WebForms public exact** (`aspnetForm`, `POST`, action =
chemin canonique du produit), entièrement fermé, avec **un seul H1**
concordant avec le titre du `Book` JSON-LD sélectionné.

Les ISBN placés hors du formulaire (navigation, autre produit, pied de
page, résultats connexes), après sa fermeture, dans un formulaire
étranger ou sur une page sans wrapper attesté, sont ignorés.
Une seconde fiche canonique reste ambiguë et provoque un refus à code fixe.
L'extraction directe hors réseau conserve son mode historique pour les
tests de fragments ; le pipeline de fiche publique réclame explicitement
le mode strict.

Tests : extrait public expurgé Blackbone (EAN papier différent de l'EPUB),
en-tête/pied de page hostiles, wrapper absent ou non clos, action/méthode
étrangère, H1 divergent, double formulaire. Aucun réseau supplémentaire,
aucune promotion des ISBN publics en format acheté ni propriété/droit
de téléchargement. Le couplage **achat authentifié → édition numérique**
continue à nécessiter une preuve réelle distincte.



## 0.1.59 — frontière de confiance JSON-LD des fiches publiques (#1886, #1072)

Les résultats d'un parseur public ne sont **pas considérés fiables par
simple transit dans un cache ou un appelant**. La projection du détail
`merge_public_detail` et l'enrichissement explicite d'un achat
`merge_attested_owned_public_detail` appliquent désormais **la même
validation stricte** des champs JSON-LD : type, bornes, ISBN-13 correct,
date ISO réelle, 1–32 auteurs textuels ; la provenance doit provenir
du sélecteur structuré exact `public_product_jsonld_Book` ou
`public_product_jsonld_Product_Book`, avec URL produit canonique,
`kind=observed_public_jsonld` et `edition_proven=false`. Les
preuves à clés additionnelles, chaînes de provenance arbitraires et
valeurs imbriquées non qualifiées sont refusées avant publication dans
les métadonnées Core ou catalogue. Les listes auteurs et preuves sont
recopiées de façon indépendante du document d'entrée pour qu'une
mutation tardive de l'appelant ne modifie pas le résultat.

Ce correctif ne transforme **jamais** un ISBN non typé en ISBN EPUB/PDF
acheté, ni une offre en droit ou téléchargement. Tests hors réseau
avec fixture de liste publique expurgée et détails JSON-LD fictifs ;
pas de changement du transport ou d'accès au compte. La recherche
avancée serveur auteur/EAN et les conditions d'accès restent non
qualifiées, indépendamment de cette barrière de projection.


## 0.1.60 — reprise bornée après HTTP 429 Numilog public (#1886)

La recherche et la fiche commerciale anonymes respectent toujours
`Retry-After` et le cooldown monotone du cache après HTTP 429, sans
reconnexion comme échappatoire. La durée fournie par le serveur est
désormais explicitement **finie, positive et plafonnée à 30 jours**.
Ce plafond empêche qu'un en-tête `Retry-After` numérique immense
(`99999...`) ou une date HTTP anormalement lointaine bloque une
session pour une durée infinie, y compris après reconnexion. Les
délais courts normaux (120 s, 180 s) restent inchangés. Les valeurs
invalides gardent le plancher prudent de 60 s.

Le cache reborde indépendamment les durées injectées avec les erreurs
HTTP (NaN, infinité, entier Python géant) sans `OverflowError` ni
blocage permanent. Aucune relance automatique, nouvelle requête,
lecture de compte, cookie ou exception fournisseur brute n'est ajoutée.
Tests hors ligne avec HTTP 429 simulé sur recherche et fiche publique,
horloge monotone déterministe et reprise au terme du plafond.

Ce correctif ne qualifie ni le respect des CGU/robots, ni la
disponibilité réelle des endpoints Numilog ni les filtres serveurs
auteur/ISBN qui restent ouverts dans #1886.


## 0.1.61 — titre obligatoire pour enrichir une carte commerciale (#1886)

La fiche commerciale anonyme est publiée par un identifiant/URL Numilog
canonique, mais **l'identité bibliographique peut diverger** de la carte de
recherche au même URL (évolution du catalogue, cache, données contradictoires).
Auparavant, une contradiction sur `JSON-LD Book.name` désactivait seulement
la copie de certains champs visibles ; les ISBN papier/EPUB, prix publics,
couvertures et offres JSON-LD de l'autre titre étaient quand même placés dans
`metadata.public_detail`.

L'enrichissement explicite vérifie maintenant la présence d'un
`Book.name` structuré, dont le titre concorde avec celui de la
carte après normalisation Unicode NFKC, casse et espaces. Un titre absent
ou contradictoire refuse **entièrement** la projection avec le code public
fixe `detail_title_conflict`. Aucun ISBN, offre commerciale, couverture,
éditeur, auteur ni champ de détail étranger n'est attaché à la carte.
La ligne originale et ses droits inchangés restent disponibles.

Les divergences **non identitaires** entre livre et carte (description,
auteur, éditeur) demeurent visibles comme `listing_conflicts`, sans
écrasement arbitraire. Tests hors ligne à partir de la fiche expurgée
Blackbone avec EAN EPUB/papier distincts, prix et couverture publics.
Aucun réseau, DRM, nouveau lien marchand, compte ou téléchargement.

Ce garde-fou n'atteste ni que la fiche soit possédée ni le format
exact acheté ; les critères de #1886 et #1072 nécessitant des preuves
externes et la recette UX restent ouverts. **Fusion après 0.1.60**.

## 0.1.62 — requalification des valeurs d'une fiche publique avant copie dans Mes achats (#1072)

Le sélecteur HTML de chaque champ public est déjà validé, mais la
`SearchResult` publique peut avoir été réutilisée ou modifiée entre
son parsing, son cache et l'enrichissement du livre acheté.
Un sélecteur correct **ne qualifie pas automatiquement sa valeur courante**.

La jointure explicite de la fiche achetée au même produit et au même
titre reste obligatoire ; puis, à l'instant de copier, les champs de
carte commerciale sont vérifiés à nouveau :
- auteur : texte borné, sans contrôles Unicode, correspondant exactement
  aux noms structurés du HTML (32 auteurs maximum) ;
- éditeur, résumé, collection : textes bornés sans contrôles Unicode ;
- date : format ISO `YYYY-MM-DD` et date réelle ;
- couverture : uniquement `https://couverture.numilog.com/<id>_GRANDE.jpg`,
  sans URL alternative, suffixe de session ni redirection.

Une valeur non qualifiée provoque le code constant
`public_edition_value_unverified`, sans exposer de contenu privé.
Il n'y a **ni publication du prix d'achat**, ni promotion d'un ISBN
papier/EPUB, ni téléchargement ou attribution de droits.
La validation des champs n'est pas une preuve de l'édition numérique
effectivement achetée ; la recette réelle #1072 demeure nécessaire.


## 0.1.63 — budget HTTP public validé avant ouverture (#1886)

Les routes recherche et détail refusent les délais non numériques, non finis,
nuls, négatifs ou supérieurs à 30 secondes avant de créer le client HTTP.
La valeur finie est transmise sans nouveau retry ni contournement du cooldown.

### 0.1.64 — budget anonyme commun (P0 #1886)

`NumilogSession.search_public(query)` (y compris `search(scope="catalog")`) et `NumilogSession.public_product_detail(url)` partagent désormais **le même cache mémoire anonyme** que l’enrichissement explicite des achats : au plus deux GET en rafale, ensuite un jeton/seconde, **un seul GET en vol**, TTL 60 secondes, refus sans boucle de retry, refroidissement HTTP 429 commun et préservé après reconnexion. Les URL de détail et les champs de recherche sont vérifiés **avant** consultation du cache. Les réponses commerciales n’utilisent ni cookies ni bibliothèque privée, et aucun droit de téléchargement n’est déduit. Ce jalon ne qualifie pas la recherche distante multi-attributs.


## 0.1.66 — collection et extrait descriptif en filtres locaux (#1886)

`NumilogPublicSession.refine_loaded_page(page, collection=..., summary=...)`
peut désormais affiner **une page commerciale anonyme déjà chargée**
sur le nom de collection publié dans
`.product_meta .LabelInfos[Collection]` et les mots contenus dans
`p.list-group-item-text` (extrait de résumé). Les champs existants
`title`, `author`, `publisher`, `year` et `format_label`
restent combinables.

Chaque valeur requiert sa preuve HTML d'origine exacte (URL de la recherche,
sélecteur et `observed_public_html`), ses types et ses limites d'origine
(collection au plus 2 000 caractères ; extrait au plus 16 000).
Un champ absent ou non qualifié augmente
`missing_evidence_count` au lieu d'être déclaré incompatible. Les compteurs
conservent le **total non filtré** de la page source et le statut
`loaded_public_page_only` : jamais d'API distante auteur/collection/résumé,
de globalité déduite, d'accès privé ou de droits d'acquisition imaginés.

Tests sur la fixture publique enregistrée `jefferson` : collection
« Romans Junior », extrait « frissonne », combinaisons de champs,
preuves manquantes/contradictoires et cache non muté. Cette livraison ne
prouve ni l'accès HTTP live, ni les conditions de robots/CGU Numilog.


## 0.1.67 — affinage local de plusieurs pages publiques déjà chargées (#1886)

Le provider `numilog-catalog` expose la méthode explicite
`refine_loaded_pages([page0, page1, ...], author=..., collection=..., ...)`
pour réunir les correspondances **sans nouvel appel réseau** sur au plus
**huit pages** obtenues via `browse_public_search` et/ou
`follow_public_link`. Elle accepte exactement les critères déjà prouvés
sur chaque page : titre, auteurs, éditeur, collection, mots du résumé, année
et format affiché. Il ne s'agit **pas** d'une recherche serveur
multi-attributs ; les champs ISBN, langue et âge restent indisponibles sans
contrat amont attesté.

La combinaison exige les pages **consécutives depuis la première**, de
**la même requête et des mêmes facettes**, avec le lien `next_url` source
exact entre pages, le même compteur public total, aucun livre en double et
toutes les règles de provenance existantes par fiche. Un changement de
requête, de facette ou de compteur refuse le regroupement plutôt que de
publier une réponse ambiguë.

Le résultat de type `PublicPagesRefinement` comporte notamment
`loaded_page_indices`, `loaded_count`, `matching_count`,
`missing_evidence_count`, `public_unfiltered_total`,
`public_results_fully_loaded` et
`has_uninspected_or_unverified_matches`. Un zéro local reste incertain
tant que la recherche publique n'est pas totalement couverte et que tous
les champs requis ne sont pas attestés. Le total publié est celui de la
**recherche publique non filtrée**, jamais un total de résultats après
filtrage local. Aucun droit `owned`, téléchargement, compte privé ou
EAN d'édition n'est projeté.

La recette CI utilise une séparation **synthétique et signalée** de
20 cartes du HTML public `jefferson` enregistré : elle vérifie des
pages successives, la provenance, les facettes contradictoires, les
duplicatas, les preuves absentes, le résultat zéro et l'absence de HTTP.
Cette fixture ne prouve **pas** qu'un second GET réel fonctionne avec
Numilog : règles robots/CGU et vraie recherche avancée restent à qualifier.


## 0.1.68 — enrichissement explicite des achats par lien produit direct (#1072)

Nouvelle opération **opt-in**
`NumilogSession.enrich_owned_direct_public_detail(item)` : un lien
`https://www.numilog.com/ID/slug.ebook` présent directement **dans la carte
du titre acheté de Mes Livres** suffit à choisir la fiche du **produit
commercial** à consulter, même lorsque celui-ci ne figure pas dans la
première page d'une recherche publique par titre.

Avant toute requête publique, la session vérifie le rattachement de
`record_id` au compte courant, **recharge les pages de Mes Livres**
avec le sélecteur strict de la carte d'origine et exige **exactement une**
édition achetée correspondante ; un compte tiers, un prêt révoqué, une
duplication ambiguë ou l'absence d'URL canonique bloquent tout GET public.
La valeur du lien ou la prétendue preuve passée dans l'objet d'appel est
ignorée. L'opération effectue au plus **un GET anonyme de fiche publique**
via le cache/budget partagé existant (TTL 60 secondes, 429, refus des
redirections), **aucune recherche publique préalable**, aucune requête
d'acquisition, téléchargement ou consultation de lien de livraison signé.

La fiche doit valider le même **lien canonique et titre** dans le Book
JSON-LD avant projection. Champs et preuves suivent la même validation
stricte que le parcours existant liste → fiche. L'origine de
l'association est enregistrée :
`public_enrichment.status=attested_owned_title_link`,
`scope=owned_library_title_link_only`, et
`public_detail_enrichment.association_evidence=numilog.library_title_link`.
**Le lien prouve seulement le produit, jamais la variante EPUB/PDF**
effectivement achetée, ni la correspondance entre EAN papier et EAN
numérique. `format_unverified=True`, `owned` (droit provenant de
Mes Livres uniquement) et `download_url=None` sont conservés ;
les identifiants papier/EPUB/PDF attestés restent *distincts dans des
métadonnées commerciales* et ne deviennent pas un droit.

Ce chemin est **distinct** de
`enrich_owned_public_detail()` (recherche puis fiche) et n'est
**pas appelé** par `library()`, `search()`, `holdings()`, la
synchronisation ou l'ouverture automatique d'une fiche. Tests synthétiques
offline : deux achats, produits homonymes, premier résultat public absent,
compte tiers, révocation, doublons, titre public contradictoire et preuve
falsifiée. Une vraie recette avec deux pages de Mes Livres authentifiées
**expurgées** reste nécessaire avant de clore le P0 #1072.


## 0.1.69 — filtrage local EAN/ISBN numérique prouvé par fiche (#1886)

Le provider public peut désormais filtrer **une page anonyme déjà chargée**
avec `refine_loaded_page(page, isbn_digital="978...")` et combiner ce
critère à `title`, `author`, etc. Ce filtre accepte uniquement un
**ISBN-13 valide** et des correspondances **exactes** déjà portées par une
fiche publique explicitement consultée (`enrich_public_detail(item)`) puis
associée à sa carte d'origine : `metadata.public_detail.public_identifiers`
et ses preuves vérifiées, rôles `epub`, `pdf` ou
`digital_unspecified`, URL et titre canonique concordants.

L'EAN `paper`, le champ `Book.isbn` JSON-LD non typé, le nom du fichier
de couverture et un `lid` privé ne sont **jamais** des preuves d'un EAN
numérique. Un identifiant absent, mal prouvé ou différent reste « inconnu »
(`missing_evidence_count`), et **non** une réponse négative exhaustive :
d'autres formats/éditions peuvent ne pas être décrits par la fiche. Le
résultat conservé est `loaded_public_page_only`, avec total du moteur
public **non filtré**, `owned=False`, `downloadable=False` et aucun
lien signé. Il n'y a ni requête réseau supplémentaire dans le filtre, ni
récupération automatique de toutes les fiches, ni prétendue recherche
**serveur** par ISBN.

**Utilisation opt-in** : appeler
`enrich_loaded_page_product(page, record_id)` pour enrichir **une seule**
carte de la page déjà chargée, puis filtrer la page retournée. Cette
opération réutilise `enrich_public_detail`, le cache et le budget réseau
anonyme commun, préserve le total et les liens de pagination, et refuse
une sélection étrangère ou une réponse tardive après déconnexion. Aucun
GET de détail n'est effectué pour les autres cartes.
Les tests offline utilisent une carte `jefferson` réellement enregistrée
et des balises EAN **synthétiques** ; aucune relation réelle entre le livre
Jefferson et ces numéros de test n'est revendiquée. La recette serveur
auteur/ISBN, les conditions d'accès et le raccordement UX restent ouverts.

## #3637 — candidats Core sélectionnés : EPUB direct et ACSM

La façade `rights_selected_delivery_candidate` exige un compte et une offre
`numilog.card:` exacts, une décision explicite de Core et un **dossier de
staging existant non symbolique**, contrôlé avant la première lecture privée.
Le GET signé reste conditionné à la relecture `MesLivres` actuelle et à
l'attestation de la même carte, édition, identité et route.

Une offre `acsm` ne peut être remplacée par un EPUB et une offre
`epub_direct` ne peut être remplacée par un jeton ACSM. Les deux parcours
produisent uniquement un fichier privé temporaire (`.acsm` ou `.epub`) dans
le staging fourni par Core, **sans** passer par `publish()`,
`publish_artifact_file()` ni l'import ADEPT legacy. La validation finale
SHA, le rapprochement aux origines et la publication sont réservés à Core.
Le canal ancien `download_direct` sans sélection demeure compatible avec
les clients historiques ; il ne vaut pas preuve de complétude ou de droit.
Les tests sont synthétiques (HTTPX/MockTransport), sans compte fournisseur.


## P0 #2797 / #3838 — Diagnostic reproductible des livraisons (31/10/2026)

La commande générique déjà disponible permet de sélectionner **un seul**
compte Numilog sans déclencher les autres fournisseurs :

```bash
cifereca sync list --provider numilog --json
cifereca sync run --provider numilog --identity '<identity_id>' \
  --credential '<credential_id>' --account-id '<account_id>' --confirm --json
```

`sync list` est une lecture locale ; `sync run` **accède au réseau**, exige
les coordonnées exactes du compte et respecte le consentement persistant ainsi
que l'interdiction de lancer deux synchronisations concurrentes. Ne jamais
inventer de consentement, relancer des actions déjà réussies sans preuve ou
publier un fichier sur la seule foi d'un reçu HTTP.

Pour analyser hors ligne l'export ZIP **brut** du journal (Diagnostics), sans
réenvoyer les liens signés ni les credentials au serveur :

```bash
python3 src/plugins/provider-numilog/numilog_delivery_diagnose.py \
  cifereca-journal-brut.zip --json
# Facultatif, isoler un run par task_ref présent dans le journal :
python3 src/plugins/provider-numilog/numilog_delivery_diagnose.py \
  cifereca-journal-brut.zip --task-ref '<task_ref>'
```

L'analyseur regroupe les événements `NUMILOG / acquisition_delivery` par
`action`, restitue chaque hop observé (`http_status`, catégories origine /
chemin / MIME, redirection, présence de cookie) puis le code terminal. Une
réponse `http_error` n'est donc plus confondue avec un échec de sélection
HTML, d'origine, d'intégrité, de fulfillment ADEPT ou de publication. Il ne
récupère **aucun** URL signé, en-tête ou secret du journal ; le ZIP original
reste privé et intact. La catégorie `other` n'identifie pas l'hôte exact :
si nécessaire, utiliser un export HTTP Trace brut protégé en `0600`
pour relever le `status` et le point de refus réseau, sans le publier.

**Attention** : `acsm_candidate` signifie seulement que le fichier temporaire
a été remis au Core. Même `acsm_complete` ou `epub_complete` côté fournisseur
ne prouve pas le **Placement** / **OriginLink** / SHA-256 final. Le diagnostic
doit être suivi de la vérification du fichier réel dans le profil et de son
lien d'origine. La recette P0 reste **six achats, six EPUB vérifiés**, dont les
deux précédemment reçus sont idempotents. Un refus HTTP est un fait, pas une
preuve que le User-Agent en est la cause.
