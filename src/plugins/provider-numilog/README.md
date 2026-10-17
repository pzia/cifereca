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
