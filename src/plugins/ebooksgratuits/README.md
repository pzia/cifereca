## #2460 — site Web public distinct de la racine COPS (21/10/2026)

Le lien générique « Site Web » du fournisseur mène maintenant à
`https://www.ebooksgratuits.com/` (page HTML publique attestée), au lieu de
`https://opds2.ebooksgratuits.org/` (racine d'hôte COPS qui s'ouvre
vide selon la recette utilisateur). Cela **ne change pas** `opds_feed_url`,
qui demeure `https://opds2.ebooksgratuits.org/index.php/feed`, un flux
**Atom/XML** destiné à un client OPDS, et non une fiche Web de livre.
La recherche par auteur, les offres EPUB et l'action `download_direct`
restent inchangées : leurs défauts réels demeurent à qualifier séparément.

---

## #1917 — rechercher une branche auteur NOM, Prénom dans OPDS/COPS (0.1.25, 20/10/2026)

Certaines réponses de recherche OPDS peuvent présenter un **sous-catalogue
de navigation**, et non les livres eux-mêmes. Quand une entrée navigable
porte explicitement un libellé de personne `HUGO, Victor`, une recherche
`Victor Hugo` ou `Hugo Victor` doit pouvoir choisir **ce lien publié**
même si la chaîne complète n'est pas littéralement contiguë.

La sélection prioritaire est strictement limitée à un **libellé de
navigation auteur à une virgule** `NOM, Prénom`, sans séparateur
multi-auteurs ni titre narratif. La route reste validée comme
**lien Atom OPDS de même origine**, dans les mêmes limites existantes
(5 niveaux, une seule branche à priorité équivalente). Chaque **livre
retourné** subit ensuite le filtre `Atom author/name` individuel :
le titre de branche seul n'atteste ni auteur d'un livre, ni résultat
global ou achat. Si plusieurs branches sont qualifiées, une seule
est parcourue avec `partial_results`. Le lien autorisé sélectionné peut naturellement faire l'objet d'un
GET de navigation (au lieu d'un refus précédent) ; aucune autre branche
n'est explorée et il n'y a ni repli réseau ni réinterrogation implicite.

Tests hors réseau (`httpx.MockTransport`) :
`tests/test_opds_author_navigation_order_p1_1917.py` :
accents et ordre des noms, branches ambiguës, auteurs étrangers,
URLs externes et avertissements sur zéro ligne. **Pas de capture
live du serveur** ; une recette autorisée reste nécessaire pour
confirmer l'impact utilisateur. Les téléchargements EPUB déjà
fonctionnels restent inchangés.

---

## #1917 / #2460 — liens Atom relatifs résolus sur le flux exact (0.1.24)

Les liens OPDS déclarés dans une page de recherche ou un
sous-catalogue COPS ne sont pas forcément absolus. Par exemple,
depuis `/index.php/feed/search/jefferson`, la référence
`../../fetch/42.epub` pointe sur `/index.php/fetch/42.epub`.
Le plugin cherchait jusqu'ici à la résoudre à partir de la **racine
configurée** `/index.php/feed` et pouvait à tort rejeter un EPUB,
une alternative HTML ou une sous-branche de recherche par auteur.
Cette incohérence pouvait produire une couverture partielle ou
une offre EPUB invisible, sans prouver la cause d'un échec live.

Désormais, `_opds_source_link` applique **deux validations** :
(1) résolution contre l'URL HTTP effective du **flux contenant le
lien** ; (2) revalidation du résultat absolu contre l'origine et
les routes autorisées de la racine OPDS configurée. Les cas déjà
fonctionnels (URL absolue ou relative à la racine) restent acceptés.
Les flux privés `account/admin/login/session/checkout` sous la
route COPS `/index.php/feed/` sont refusés, comme les chemins
traversants encodés. Aucune requête additionnelle, droit privé,
nouvelle route serveur ou téléchargement automatique.

Le même contrat s'applique à la recherche OpenSearch, aux liens
de sous-catalogue, à la reconnaissance d'une entrée Book, aux
offres EPUB, aux liens HTML alternatifs et aux miniatures. Le
post-filtre auteur reste **limité aux pages chargées** et conserve
son avertissement `partial_results` : cette correction ne prouve
aucune couverture exhaustive ni changement du serveur COPS.

Tests déterministes avec `httpx.MockTransport` :
`tests/test_opds_relative_link_source_p1_1917.py`.
Une recette sur une notice récente et une notice en échec,
avec réponse fournisseur autorisée, reste à produire (#2460).

---

## #2460 / #1917 — sécuriser les chemins d'acquisition EPUB OPDS (0.1.23)

Les URLs d'acquisition COPS acceptées sur `/index.php/fetch/...`
doivent identifier **le même point d'accès après décodage du chemin**.
Le contrôle précédent vérifiait surtout le préfixe littéral :
`/index.php/fetch/%2e%2e/account/...`, `%2f`, `%5c` et les
séquences doublement encodées `%25` pouvaient être interprétées comme
une autre route après validation du fournisseur. La version 0.1.23
refuse ces chemins **avant** de les publier dans une notice et
**à nouveau** lors d'un téléchargement explicite depuis le cache.

Les routes COPS déjà connues telles que `/index.php/fetch/42.epub`,
`/index.php/fetch?id=42` et `/fetch.php?id=42&format=epub`
restent admises (sans présumer de leur disponibilité réseau réelle).
Ni l'EPUB ni la fiche HTML ne sont inventés ; aucun nouveau GET,
aucun retry ni changement de permissions ou de politique de
redirection `same_origin`. Les téléchargements déjà fonctionnels
ne doivent pas être affectés. Une URL ambiguë rend l'offre non
qualifiée, pas le catalogue entier « indisponible ».

Tests synthétiques et hors réseau :
`tests/test_opds_encoded_acquisition_integrity_p1_2460.py`
(entrée Atom, ligne cachée en cache, et routes EPUB conservées).
La cause des téléchargements échouant en réel et la recette GTK
demeurent à qualifier dans #2460.

---

## #1917 — auteurs OPDS conservés individuellement (0.1.22, 19/10/2026)

Retour utilisateur : la recherche par auteur est « partielle ou inefficace ».
La réponse OPDS peut afficher `HUGO, Victor`, tandis que l'utilisateur
saisit `Victor Hugo`. Le post-filtre historique cherchait la chaîne
complète dans le seul libellé `author`, ce qui refusait silencieusement
cette permutation pourtant valide.

Le plugin conserve désormais la liste des **noms individuels**
`opds_author_names` issus exclusivement des `<author><name>` Atom
de la notice. Le filtre `field=author` compare une chaîne
insensible à la casse et aux accents, puis, pour **deux mots ou plus**,
accepte la permutation des mots attestés **dans le même auteur**.
Aucune jointure de deux personnes pour créer un faux auteur, aucune
inférence depuis le titre/éditeur, aucun alias inventé et aucune
requête réseau supplémentaire. Les noms et leur preuve
`opds.atom.author.name` restent des **métadonnées publiques**.

**Attention :** c'est un **filtre local sur la page OPDS effectivement
reçue**, pas une route serveur auteur qualifiée. Le signal
`partial_results` est conservé, y compris si la page filtrée est
vide. Une recherche nom complet/prénom inversé ne prouve ni le
contenu des pages suivantes, ni l'exhaustivité du catalogue COPS.
Tester la route live autorisée, les sous-catalogues et l'UI Core
reste nécessaire pour fermer #1917.

Tests hors réseau `test_opds_author_order_p1_1917.py` :
auteurs inversés, accents, plusieurs auteurs distincts, titres
trompeurs, absence de preuve et absence de GET supplémentaires.
Ne pas considérer ces fixtures synthétiques comme une capture
XML réelle de COPS.

---

## #2460 — fiche HTML distincte du flux OPDS et acquisition EPUB (0.1.21)

Correctif du **retour utilisateur** « Voir en ligne ouvre le XML » :
le plugin publiait `friendly_url = alternate or feed_url`. Cette
valeur transformait une URL Atom de recherche/navigation en prétendue
**fiche Web du livre**. Désormais seule une **alternative
`rel=alternate` explicitement `text/html` ou `application/xhtml+xml`**
et qualifiée sur la même origine publique, sans query privée et hors
routes XML/feed/fetch/account, devient `friendly_url`. Une fiche
sans alternative HTML conserve `friendly_url=None`, donc aucune action
« Voir en ligne » trompeuse ; le lien de téléchargement EPUB reste
séparé sous `download_url` et n'est jamais construit depuis le titre
ou l'ID de l'entrée.

Pour l'acquisition **explicitement déclenchée**, la notice publique
est envoyée au **même broker `transport.http`** que la recherche : le
plugin autorise au maximum **deux redirections** sous une nouvelle
politique `redirect_policy=same_origin` imposée à chaque saut par
Transport HTTP **1.0.5**. Une cible d'une autre origine, un downgrade
HTTPS, une réponse HTML, un EPUB invalide ou un dépassement de taille
reste refusé avant publication atomique. `classify_http_status=True`
donne des codes stables et expurgés `auth_required`, `access_denied`,
`not_found`, `rate_limited`, `service_unavailable`, sans corps HTTP.
Les fournisseurs qui ne demandent pas cette politique conservent
leur comportement historique.

La version **0.1.21 dépend de Transport HTTP >=1.0.5** pour éviter
qu'un ancien broker suive des redirections sans appliquer la
restriction d'origine. Depuis une notice OPDS sans fiche HTML,
l'origine de fichier retournée après téléchargement est la **vraie URL
EPUB qualifiée**, jamais le flux XML ni une page générique inventée.
Les limites globales d'acquisitions **5/min et 50/jour/personne**
restent sous le pilotage du Core, sans déclenchement automatique.

Tests hors réseau :
`test_opds_web_vs_acquisition_p1_2460.py`,
`test_opds_same_origin_redirect_p1_2460.py` et test existant
`test_opds_calibre_1084.py`. Toutes les notices positives sont des
fixtures Atom **synthétiques** ; ni la raison d'un échec réel sur un
livre donné, ni la présence effective d'une page HTML chez COPS,
ni les actions GTK de la release installée ne sont attestées.
Le P1 #2460 reste **ouvert** pour recette réseau autorisée/expurgée,
validation Recherche et Recommandations #1354 et contrôle CI/GTK.

---

## #1917 — reconnexion réelle du client public (0.1.20, 19/10/2026)

`EbooksSession.close()` ferme définitivement son `httpx.Client`.
Jusqu'ici, un `connect()` ultérieur réactivait le drapeau local et
retournait « Catalogue public » sans recréer le client : une recherche
hors cache pouvait alors échouer sur une session HTTP définitivement
fermée. Cette incohérence touche OPDS et l'ancien backend HTML de
simulation, indépendamment de la couverture OPDS distante.

Désormais, un `connect()` explicite sur **la même session déjà fermée**
crée un **nouveau client HTTP anonyme** (même configuration
`timeout_seconds`, `trust_env=False`, `follow_redirects=True`).
Une connexion répétée sur un client actif ne recrée rien.
Une erreur de construction se traduit par
`Ebooks libres et gratuits : transport_unavailable` sans publier
une exception contenant chemins locaux ou jetons : la session reste
déconnectée, sans compte fictivement actif.

**Important** : `connect()` reste une **inscription locale**, sans GET
ni promesse de disponibilité du catalogue. Seul `search()` effectue
les lectures OPDS/OpenSearch publiques réellement nécessaires.
Tests `test_opds_reconnect_http_client_p1.py` : recherches nouvelles
après fermeture/reconnexion, renouvellement du transport,
absence d'appels au connect, refus hors session, backend HTML
indépendant et panne de réouverture expurgée. Tout est testé avec
`httpx.MockTransport`, sans HTTP réel, compte privé ou téléchargement.

Cette modification n'affirme pas que les notices nouvelles apparaissent
déjà dans CoreUI (#2361) ni que le serveur OPDS est disponible en live.
Le ticket #1917 reste ouvert.

---

## #1917 — lecture OPDS bornée pendant le GET (0.1.19, 19/10/2026)

Le catalogue COPS imposait déjà **2 Mio** au XML après lecture, mais
`httpx.Client.get()` avait auparavant **tout chargé en mémoire** avant
ce contrôle. La limite n'était donc pas un véritable budget réseau.

`_opds_fetch()` lit désormais la réponse via `stream()` et
`iter_raw(chunk_size=65536)` avec plafond de **2 Mio d'octets reçus**,
y compris lorsqu'un `Content-Length` trompeur annonce une taille faible.
La négociation utilise `Accept-Encoding: identity` ; une compression
`gzip` / `deflate` reçue malgré cette demande est **refusée** avant
décompression pour éviter une réponse comprimée minuscule produisant une
expansion arbitraire hors budget. Le flux est fermé immédiatement au
dépassement. Aucune destination nouvelle ni repli HTML n'est permis.
**Compatibilité tests/mock** : les réponses `httpx.Response(content=...)`
déjà intégralement lues avant de parvenir au plugin restent acceptées,
mais avec validation stricte des octets et du XML. Cela ne transforme
pas un transport réellement prébufferisé en transport borné avant mémoire ;
les vrais GET OPDS utilisent toujours le streaming brut.

Le parseur `xml.etree.ElementTree` se voit imposer un constructeur
d'arbre refusant **tout DOCTYPE** au niveau XML (y compris pour
les documents **UTF-16**, que les précédentes recherches
`b'<!doctype'` ne pouvaient pas détecter), ainsi qu'un budget de
**12 000 éléments / 96 niveaux**. Ces contrôles sont appliqués pendant
le parse, non après la construction de l'arbre, et renvoient le même code
`invalid_opds_feed` sans imprimer le XML fournisseur.
Les corps HTTP des redirections 3xx ne sont pas lus ; la cible reste
qualifiée sur la même origine et dans les routes OPDS autorisées.
401/403/429/5xx et XML/DTD invalides conservent leurs codes sûrs.

Tests hors réseau `test_opds_streaming_byte_budget_p1.py` : page Atom,
limite exacte, flux trop volumineux avec en-tête mensonger,
gzip hostile sans lecture, redirection autorisée/étrangère, erreurs
serveur expurgées, DTD UTF-8/UTF-16 interdite, limites de largeur et de
profondeur XML, réponses MockTransport déjà bufferisées. Tests via
`httpx.MockTransport`,
sans compte, téléchargement ni trafic distant.

**Limite** : une installation qui sert uniquement des réponses OPDS
compressées malgré `Accept-Encoding: identity` sera classée
`invalid_opds_feed` ; un support compressé sûr exigerait plus tard une
décompression incrémentale strictement plafonnée et des captures
amont. Cette modification ne prouve pas l'accessibilité live d'OPDS
ni la recherche hors cache dans CoreUI.

---

## #1917 — qualification des entrées Atom EPUB/PDF (0.1.18, 19/10/2026)

Un `<entry>` OPDS n'est plus considéré comme un livre sur la seule
présence d'un `rel=.../acquisition`. Une offre admissible doit désormais
déclarer un type de fichier reconnu (EPUB, PDF, Mobipocket) **et** un lien
d'acquisition direct répondant aux mêmes restrictions d'origine et de
chemin public `/index.php/fetch` que le téléchargement réel ; un
`rel=alternate` EPUB doit satisfaire les mêmes restrictions.

Cela empêche une entrée provenant d'un autre hôte, d'une URL
`/account/`, d'un MIME HTML ou d'un lien alternatif privé de se
faire passer pour un livre et de détourner la navigation vers le
catalogue global. Les sous-catalogues de navigation ne sont plus
projetés comme de fausses notices de livres.

Sur un flux mélangeant des entrées d'acquisition valides et des
sous-catalogues, le plugin affiche `partial_results` : seules les
notices directes sont couvertes, aucun parcours automatique de la branche
voisine n'est ajouté. Une fiche **PDF seulement** reste visible, mais
`downloadable=False` et sans lien EPUB. Le résultat est borné au flux
reçu, sans promesse d'exhaustivité ni nouveaux appels HTTP de recherche.

Tests hors réseau `test_opds_public_book_entry_integrity_p1.py`
avec `httpx.MockTransport` : origines étrangères, liens privés,
MIME trompeur, page mixte, PDF-only et racine OPDS. La réponse réelle
`opds2.ebooksgratuits.org` **n'a pas été vérifiée en direct** par ces
tests ; la recette hors-cache dans le Core/UI et la qualification réseau
restent ouvertes dans #1917 et #2361.

---

## #1917 — navigation OPDS racine et preuve de couverture (19/10/2026)

En mode OPDS Calibre/COPS (par défaut), `search("")` consulte la racine
et choisit la branche « All books / Tous les livres » lorsqu'elle est
**identifiable de manière unique**. Si la racine ne contient que des sous-
catalogues thématiques (« Authors », « Recent », etc.), le plugin peut en
visiter **un seul**, mais signale désormais `partial_results` même lorsque
cette branche répond avec zéro livre. Deux branches « All books » distinctes
sont également signalées comme une couverture partielle ; aucun crawl ou
concaténation spéculative n'est ajouté.

Cette correction ne prouve **pas** que le serveur distant est disponible ni
que la recherche OpenSearch est intégrée au moteur fédéré. Le succès d'un
téléchargement depuis le cache local est indépendant. Tests hors réseau :
`tests/test_opds_root_partial_1917.py`. Recette live expurgée et GTK :
suivis par #1917 et le contrat Core #1916.

# Acquisition Calibre OPDS — #1084 (0.1.17)

Production : `https://opds2.ebooksgratuits.org/index.php/feed` (COPS, OPDS Atom/XML 1.x, **pas** OPDS JSON 2). Le site officialise cette URL : https://www.ebooksgratuits.com/torrent.php. Recherche via le lien OpenSearch annoncé par la racine ; navigation via liens Atom et pagination par `rel=next` uniquement. Limite de 101 pages demandables ; aucune exploration ou acquisition implicite. Le catalogue Calibre contient aussi des ouvrages de partenaires, donc éditeur renseigné seulement lorsque la notice l'atteste. Titres, auteurs, catégories, genres, mise à jour et liens EPUB proviennent de chaque notice ; pas d'âge inféré depuis `Jeunesse` ni de jugement territorial ou date historique déduits. Les champs spécialisés filtrent localement la page de recherche et annoncent une couverture partielle. La présence d'une offre EPUB est vérifiée par son lien et type MIME, le transport valide le fichier ; des notices sans EPUB restent non téléchargeables.

`acquisition_backend=opds` est le défaut. `opds_feed_url` permet un catalogue de test autorisé. `acquisition_backend=html` conserve l'ancien parseur uniquement pour les tests de régression et les simulateurs locaux ; `base_url` ne pilote que ce mode. Aucun basculement automatique vers HTML si l'OPDS est indisponible : erreur explicite. Recette hors ligne par `test_opds_calibre_1084.py`. Recette live sur le flux tiers non revendiquée ; si le site modifie son contrat, qualifier un échantillon réel avant tout correctif.

---

## Notes historiques du parseur HTML (mode simulation)
# Ebooks libres et gratuits — provenance des dates et droits (0.1.12)

## Audit des métadonnées amont #764 — 11 octobre 2026

Source publique officielle : [liste des ebooks](https://www.ebooksgratuits.com/ebooks.php),
[fiches Nouveaux contes du Far West](https://www.ebooksgratuits.com/details.php?book=3500)
et [Le Hussard bleu](https://www.ebooksgratuits.com/details.php?book=3498).

**Preuves sur les pages publiques** : les fiches et les cartes de catalogue
affichent **« Parution le 31/08/2026 »** pour les nouveautés (date de
mise à disposition de l'**édition numérique Ebooks libres et gratuits**)
et une liste de plusieurs formats (ePub, PDF, HTML, etc.) avec tailles.
La fiche du *Hussard bleu* avertit explicitement :
**« téléchargement non autorisé dans certains pays »** et affiche
la date de décès de l'auteur. Ni la date d'origine de l'œuvre, ni les
droits applicables dans le pays de l'utilisateur ne peuvent être déduits
de ces éléments.

### Implémentation conditionnelle

L'ancien parseur conservait titre, auteur, description, image, fiche et
lien EPUB ; il **ne projetait ni la date de parution de l'édition sur ce
site ni son avertissement territorial**, même lorsqu'ils apparaissaient
dans la liste déjà téléchargée.

Désormais, sans aucun nouvel appel HTTP et pour **chaque carte isolément** :

- `metadata.ebook_release_date = YYYY-MM-DD` uniquement si le texte
  contient un libellé explicite `Parution le JJ/MM/AAAA` et si la date
  est valide. La preuve `ebook_release_evidence =
  ebooksgratuits.catalogue.parution` accompagne la valeur.
- `SearchResult.published_at` **reste absent** : ce champ ne doit pas
  confondre la parution numérique et la publication bibliographique
  originale ou l'édition imprimée.
- `metadata.territorial_rights_warning = true` uniquement pour
  l'avertissement textuel explicite que le téléchargement n'est pas
  autorisé dans certains pays ; preuve
  `territorial_rights_evidence = ebooksgratuits.catalogue.warning`.
  Le champ `license_note` affiche un message non ambigu.
- **Aucune décision juridique automatique** : le plugin n'affirme ni
  que le livre est libre de droits dans un pays donné, ni qu'il y
  est interdit. Le téléchargement reste **sur action explicite**,
  soumis aux restrictions légales et au transport existant.
- Aucune date de décès, aucun âge recommandé, aucun ISBN, aucun contenu
  de commentaire ou script n'est transformé en métadonnée. Les textes
  de carte sont utilisés transitoirement et **ne sont pas sauvegardés**
  dans `metadata`. Analyse bornée à 12 000 caractères de texte/carte.

Les filtres publics attestés Genre/Catégorie et la sélection du format
EPUB existants restent inchangés.

### Tests et limites

`tests/test_release_rights_provenance_p2.py` couvre les trois états
**date valide / date invalide / date absente**, l'alerte ciblée par livre,
la distinction entre date de l'œuvre et date de l'édition, le texte HTML
fragmenté et l'absence de fuite des textes de commentaires/scripts.

Les fragments HTML de tests sont **synthétiques**. Les pages officielles
consultables établissent l'existence et le sens des libellés, **mais pas
la structure HTML brute exacte**. Une capture DOM publique expurgée
reste nécessaire pour une recette bout en bout du nouveau parseur.

### Autres champs amont observés (non livrés)

Les pages affichent aussi des tailles pour plusieurs formats, des
catégories, genres et des liens vers une page de droits.
Une extension de ces champs exigerait la vérification des ancres
effectives et de leur correspondance dans le HTML de catalogue ;
**ne pas** attribuer une catégorie ou une date de publication
historique sur la simple base du titre.


## Couverture des sélecteurs de recherche (#1917, 0.1.13)

Lorsqu'un texte auteur, catégorie ou genre correspond à plusieurs options
**également classées dans le même sélecteur** (ex. `Jean Alpha` et
`Jean Beta` pour `jean`), la recherche conserve son choix déterministe
mais annonce `partial_results : plusieurs options du filtre ; une seule interrogée`.
Une option exactement égale au texte demandé prime sans fausse ambiguïté.
En mode sujet, la perte d'une deuxième voie catégorie/genre reste signalée
indépendamment. Aucun scan réseau supplémentaire, aucune revendication de
recherche exhaustive, et aucun nouveau filtre serveur n'est introduit.


## Pages de recherche bornées (#1917, 0.1.14)

Les recherches titre, auteur, catégorie, genre et sujet utilisent un index de
page **entier** `0..100`, déclaré dans les capacités de recherche
(`max_pages=101`). Les index négatifs, booléens, chaînes, fractions et pages
hors limite sont refusés avant tout GET/POST avec le code public
`invalid_page`, au lieu de réinterpréter la demande en page zéro.
Les avertissements de recherche sont réinitialisés pour les requêtes
refusées ; aucun scan des pages suivantes n'est déclenché.


## Valeurs globales des sélecteurs (#1917, 0.1.15)

Les choix par défaut du formulaire HTML (`id_auteur=0`,
`id_categorie=1`, `id_genre=1`) signifient **Tous/Toutes**, et ne sont
pas des valeurs attestant un auteur, une catégorie ou un genre. Leur
libellé ne participe plus à la résolution d'une recherche spécialisée :
la requête `author=Tous` ne lance plus une recherche globale présentée
comme filtrée. Un sélecteur ne contenant que cette option de défaut
est traité comme `field_route_unavailable`, et non comme un résultat
vide obtenu par interrogation réelle d'un champ. Aucun appel HTTP
supplémentaire ni changement d'acquisition n'est introduit.

Tests offline : refus des trois valeurs globales et des sélecteurs
contenant seulement un choix par défaut. Les identifiants sont ceux
observés par l'adaptateur actuel, sans hypothèse sur de futurs schémas.

---

## #2460 — téléchargement EPUB : échecs classés sans données privées (0.1.26)

Les recherches OPDS publient les liens EPUB **exacts** et l'action « Voir en ligne »
reste absente sans fiche HTML explicite. Les téléchargements déjà fonctionnels
restent inchangés. En cas d'échec du broker public `transport.http`, le plugin
émet désormais un **code stable** sans URL, titre, compte, contenu de réponse
ou chemin : `download_auth_required` (401), `download_access_denied` (403),
`download_not_found` (404/410), `download_rate_limited` (429),
`download_service_unavailable` (5xx/transport), `download_redirect_denied`,
`download_redirect_limit`, `download_invalid_content` (HTML/MIME),
`download_invalid_epub`, `download_protected`, `download_too_large`,
`download_empty_response`, `download_destination_conflict` et les replis
fermés `download_rejected`/`download_unavailable`.

Ces codes catégorisent **une tentative**, et ne prouvent ni une indisponibilité
générale ni la suppression d'une notice. Aucun retry, élargissement d'origine,
contournement du refus HTTP, téléchargement implicite ou changement de droit.
Annulation préservée. Tests entièrement hors réseau couvrent une notice fraîche
et sa copie en cache, chaque classe d'erreur et le parcours réussi. Un comparatif
avec deux vraies notices (une réussie, une échouée) reste nécessaire pour isoler
la cause des défaillances rapportées sur le poste utilisateur.

## #1917 — limite locale de page OPDS (0.1.28)

Si une page serveur contient plus de livres correspondant au filtre que la
limite locale, le fournisseur publie `partial_results` : des notices de
cette page ne sont pas affichées. La page API suivante suit le lien OPDS
`next`, pas la fin masquée de la page précédente : elle ne garantit donc
pas de retrouver ces notices. Aucun parcours supplémentaire ni téléchargement
automatique. Le filtre auteur reste limité à la page reçue et conserve son
avertissement propre. Un résultat dans la limite ne reçoit pas ce nouvel
avertissement ; les avertissements sont réinitialisés à chaque recherche.

Tests Atom/MockTransport synthétiques : 0/1/2/3 livres pour une limite de 2,
liens EPUB préservés, avertissement retiré à la recherche suivante et limite
appliquée après filtrage auteur. Ceci ne prouve pas la cause de la recherche
auteur terrain ni la disponibilité HTTP des téléchargements (#2460).
