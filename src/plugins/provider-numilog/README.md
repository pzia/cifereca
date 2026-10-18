# Numilog — provenance des URL de livres (0.1.16)

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
