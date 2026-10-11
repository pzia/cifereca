# Critique littéraire

Plugin prospectif à exécution basse pour agréger des critiques publiques et les relier à des livres sans rapprochement opaque.

## Contrat source `cifereca.criticism-source/1`

Une source est **structurelle** : elle n'a pas besoin d'importer le Core ni ce plugin. Elle expose trois méthodes :

- `source_info()` → objet/mapping avec `source_id`, `display_name`, `homepage_url` optionnelle ;
- `discover(query, limit=20)` → prévisualisation de sources/articles, sans activation automatique ;
- `collect(cursor=None, limit=50)` → iterable ou mapping `{items, next_cursor?, fresh_for_seconds?}`.

Chaque item collecté doit fournir au minimum `title` et une URL HTTP(S) canonique. Les champs normalisés reconnus sont : auteur/date de critique, extrait, langue, titre/auteurs du livre, ISBN valides et URL(s) de notice.

Les exceptions d'une source sont isolées dans un `CollectionReport` et ne cassent pas les autres sources.

## Découverte RSS/Atom

`discover_feeds()` reçoit une fonction de métarecherche injectable. Le plugin détecte :

- les URL de feed explicites retournées par la métarecherche ;
- les `<link rel="alternate">` RSS, Atom et JSON Feed dans le HTML fourni.

La découverte ne fait aucune requête réseau d'elle-même et n'active jamais automatiquement une source.

## Ingestion

`CriticismIndex` canonicalise les URL et retire les fragments, déduplique un même article tout en conservant sa provenance multi-source, rejette un item invalide sans annuler le lot, conserve une échéance de fraîcheur fournie par la source et ne stocke pas le corps intégral d'un article.

## Rattachement aux livres

Ordre de preuve : ISBN-10/13 valide commun (100), URL de notice canonique commune (90), titre exact + auteur exact (75), puis suggestions faibles. Deux livres obtenant la même preuve forte sont rétrogradés en suggestions `ambiguous`; aucune fusion silencieuse.

## Périmètre

Ce groupe traite #710, #711, #712 et #713. La vue depuis la fiche livre (#714) n'est pas implémentée ici : elle dépend du point d'extension public demandé au Core par #715.


## Suite de la PR #736 : utilisation de la page (0.2.0)

Activer **Critiques littéraires** dans le gestionnaire. Depuis une fiche livre,
« Consulter les critiques » ouvre la page et son cache local, sans requête distante.
Filtres : source, langue exacte et date minimale ISO. La preuve, le statut
confirmé/suggestion, l'auteur de critique, la date, l'extrait et le lien navigateur
restent visibles. Le retour à la fiche existante utilise facultativement #737.

Coller l'URL d'une page ou d'un flux, **Prévisualiser**, puis **Ajouter**. L'abonnement
reste désactivé jusqu'à la coche explicite. **Actualiser les sources actives** lance
la collecte dans le broker de tâches. Les plugins tiers `criticism.source` sont
résolus par contrat/version ; une panne n'interrompt pas les autres sources.

La source fournie lit RSS 2, Atom et JSON Feed (2 Mio maximum, délais et redirections
bornés). Pas de téléchargement d'article intégral. Les résumés sont convertis en
texte et limités à 600 caractères. Le cache et les abonnements persistent dans le
répertoire de données du plugin ; aucune identité ni livre sélectionné n'y est stocké.

Le flux doit attester les métadonnées du livre : RSS/Atom reconnaît `dc:identifier`
`urn:isbn:…` et `https://cifereca.org/ns/book/1` (`title`, `author`, `notice`). JSON Feed
accepte `_cifereca_book`. Le titre de l'article et son auteur ne deviennent jamais
implicitement ceux du livre. Les flux ordinaires sans ces preuves sont collectés
mais peuvent ne correspondre à aucun livre.

L'équivalence ISBN-10/13 est normalisée. Des ISBN explicitement contradictoires ne
sont plus confirmés par un simple titre/auteur commun. La découverte par
métarecherche reste l'interface injectable décrite ci-dessus ; aucun moteur distant
n'est présélectionné. La page fournit la découverte par URL explicite.

La section « Périmètre » ci-dessus décrit le socle #736 ; la présente suite implémente
#714 via les contributions publiques `book.detail` déjà disponibles. La validation
locale reste sans GTK ; la recette visuelle est suivie dans #716.


## Durcissements source RSS/Atom 0.3.0

La source fournie applique désormais une frontière **publique** avant tout accès réseau et après chaque redirection :

- refus de `localhost`, `.local`, `.localdomain` ;
- refus des IP littérales non globales (loopback, privées, link-local, réservées, multicast, etc.) ;
- refus des redirections vers une cible non publique ;
- revalidation de la destination finale, y compris avec un transport injecté ;
- transport HTTP(S) interne sans proxy d’environnement, avec résolution DNS publique épinglée sur l’adresse TCP effectivement connectée ;
- liens article et notice contenus dans RSS/Atom/JSON Feed filtrés par la même règle ;
- racine JSON Feed obligatoirement objet et `items` obligatoirement liste ;
- URL manquante conservée vide : elle n’hérite jamais de l’URL du feed.

Les limites existantes restent inchangées : 2 Mio maximum, cinq redirections au plus, aucun corps d’article téléchargé, DTD/entités XML refusées.


### Validation P0 du transport RSS

Le chemin de production de `FeedSource` utilise directement `fetch_public` : il ne dépend pas du client `httpx` injectable réservé aux tests historiques. Les tests de non-régression du transport couvrent maintenant ce chemin par défaut et vérifient qu’un nom public résolu vers une adresse privée est refusé avant création du socket, puis qu’un peer différent de l’adresse DNS validée est refusé avant TLS/HTTP.

Le transport épinglé est local à `criticism_feeds.py`. La recherche de références dans le dépôt ne montre pas de réutilisation par d’autres plugins : les autres chemins réseau restent donc hors périmètre de #824 et doivent être audités séparément s’ils nécessitent la même garantie.


## Résilience du cache et erreurs expurgées — #709 (0.3.4)

Une collecte demandée explicitement mais composée exclusivement de notices
**invalides** ne constitue pas une preuve d'absence de critiques : une mise à
jour `replace_source=True` conserve les articles et la fraîcheur déjà
connus. Une collecte **réellement vide, valide** reste autorisée à vider la
source, sans effacer les autres sources ni la provenance des articles partagés.
Un lot mixte garde ses notices valides ; les erreurs ne bloquent pas les
sources indépendantes.

Les descriptions brutes d'exceptions (transport, adaptateur, normalisation)
ne sont plus incluses dans `CollectionReport.errors` : seuls des codes
stables `item[N]: invalid_article`, `collect: source_error` et
`source: incompatible` peuvent être remontés. Aucun token, URL signée ni
autre contenu privé ne doit fuiter dans le rapport d'erreur.

Tests hors réseau : `tests/test_cache_resilience_p2.py`. Le plugin ne
collecte jamais automatiquement à l'ouverture d'une fiche livre.

## Pages de critiques bornées — P2 #709 (0.3.5)

La limite donnée à `collect_source(limit=N)` (1 à 500) est aussi **vérifiée
à la réception** : le plugin ne parcourt qu'au plus N+1 éléments d'un
itérateur tiers et refuse sans ingestion une page trop grande
(`collect: limit_exceeded`). Un objet, une chaîne, `null`, un curseur
non conforme ou un fournisseur non itérable ne devient jamais une page
vide donnant prétexte à supprimer un cache valide.

Une page vide réellement attestée reste capable de remplacer une
source, conformément au contrat précédent. Les expirations déclarées
par une source sont plafonnées à sept jours pour éviter dates invalides
ou fraîcheurs pratiquement infinies. Les exceptions brutes des
fournisseurs ne sont pas divulguées dans les rapports.

Pas de collecte au simple affichage d'une fiche ; la pagination
multi-source reste explicitement plafonnée à cinq pages dans le worker.
Tests déterministes sans réseau dans
`tests/test_collection_page_bounds_p2.py`.
