# Constellations

Plugin prospectif de navigation relationnelle entre livres.

## Graphe explicable

Le moteur consomme des références de livre structurelles compatibles avec le `BookRef` public et leurs métadonnées. Il déduplique d'abord par identifiant attesté (ISBN, provider + identifiant source). Le repli titre + auteur est limité aux notices **toutes deux opaques** : une notice sans identifiant ne peut jamais être fusionnée silencieusement avec une édition fortement identifiée. Les clés `isbn`, `isbn10`, `isbn_10`, `isbn13` et `isbn_13` sont canonicalisées vers un ISBN-13 valide.

Chaque relation conserve ses raisons et son poids : auteur, série, sujet, période, langue, longueur, format, humeur ou proximité éditoriale. Aucun score opaque n'est utilisé.

L'état d'accès reste explicite : `owned`, `borrowable`, `free` ou `external`.

## Modes

- **Confort** : privilégie les voisins proches.
- **Explorateur** : augmente la distance contrôlée avec le curseur proche ↔ surprenant.
- **Projet** : conserve un sujet partagé et favorise une progression lorsque le niveau de difficulté est attesté.
- **Humeur** : utilise uniquement des tags humeur/tonalité qualifiés ; les attributs marqués comme inférés restent signalés comme tels.

## Promenade

`promenade()` construit une chaîne de 4 à 8 livres entre deux points et propose les variantes `short`, `balanced` et `surprising`. Chaque transition expose ses raisons. Les répétitions d'auteur ou de série sont pénalisées. `replace_step()` permet de remplacer une étape intermédiaire sans recalculer les autres étapes.

## Cache

`ConstellationService` isole son cache par identité. La réutilisation n'est autorisée que si **l'identité et un `freshness_token` non vide** sont fournis. Sans preuve de fraîcheur, le graphe est reconstruit et un ancien cache de cette identité est écarté. `invalidate()` reste disponible pour l'invalidation explicite.

## Périmètre

Ce groupe traite #701, #702 et #703. La vue spatiale GTK #704 reste séparée et dépend des contrats publics Core suivis dans #705. Le plugin n'importe aucun module privé `cifereca.*` et ne déclenche aucune acquisition implicite.

## Vue interactive — suite de #738 (0.2.0)

La page `ui.page.constellations` ajoute l'action **Explorer les liens** depuis une
fiche livre. Elle réutilise intégralement `ConstellationGraph` de #738 pour les
relations, modes, parcours et remplacements ; l'adaptateur ne fait que projeter les
notices publiques `BookRef` et les libellés.

Choisir un profil lit sa bibliothèque locale. **Chercher en ligne** est explicite.
Le détail central utilise le composant public standard, les voisins sont des boutons
avec raisons et état de disponibilité. La grille s'adapte à une seule colonne sur
petite largeur et conserve la navigation clavier. Historique précédent/suivant,
quatre modes, curseur proche/surprenant, départ/arrivée et variantes de promenade.
L'URL affichée est uniquement celle de la notice fournie par la source.

Le retour à la fiche Bibliothèque existante consomme facultativement la navigation
publique de #737. Il respecte le profil demandé ; sans résultat, un message indique
que la notice locale n'est pas disponible.

Le snapshot local est mis en cache par identité, invalidé par les notifications de
l'hôte et par Actualiser. Toute réponse tardive d'un ancien contexte est ignorée.
Pour borner le coût quadratique du moteur, cette version affiche explicitement sa
limite : 300 notices locales + 100 résultats de recherche. La promenade borne
également le nombre d'expansions, même si l'arrivée est inaccessible.

Corrections du socle : catégorie `navigation.constellation` déclarée ; suppression
de la référence `_ISBN_CLEAN` non définie ; équivalence ISBN-10/13 ; identifiants de
livre distincts non fusionnés sur un simple titre/auteur commun ; pages négatives
non interprétées comme une lecture courte.

Limite Core suivie dans #705 : aucun contrat public ne fournit encore les résultats
calculés de Recommandations. La page utilise les snapshots locaux et les catalogues,
pas les objets privés de ce plugin. La recette visuelle GTK reste dans #706 ; les
tests locaux couvrent le moteur, les projections et les callbacks sans prétendre
constituer une recette GUI.

### Réactivité des calculs

Les nouveaux voisinages, promenades et remplacements d'étape sont calculés via le
broker de tâches, hors du fil GTK. Les callbacks ne publient que le résultat du
contexte courant : changement de profil, nouvelle sélection et désactivation
invalident les résultats tardifs. L'exploration de promenade observe l'annulation
entre les expansions. Les listes d'adjacence sont construites une fois par graphe
et réutilisées ; chaque étape n'effectue plus un scan et un tri de tous les livres.
Les tests utilisent une tâche à exécution différée et un dispatcher explicites,
pour distinguer le calcul de l'application UI sans prétendre valider GTK.


## Durcissements moteur 0.3.0

- prise en charge canonique des alias ISBN-10/ISBN-13 dans `identifiers` ;
- aucune fusion titre+auteur entre une notice opaque et une édition à identité forte ;
- le repli titre+auteur reste possible uniquement entre notices également opaques ;
- aucun cache réutilisé sans identité **et** jeton de fraîcheur explicites ;
- une identité vide n'est jamais mise en cache ;
- les relations, classements, promenades et états de disponibilité restent inchangés.

## Présentation du voisinage — P2 #762 (0.3.1)

La barre d'actions est regroupée par intention **Explorer**, **Chercher**, **Parcours** ; les extrémités **Proche** et **Surprenant** ainsi que la valeur 0–100 sont visibles. Le panneau de détail ne prétend plus qu'il n'existe aucun voisin lorsque la liste du composant commun est vide : les voisins se trouvent dans une grille distincte avec un compteur explicite.

Chaque carte sépare titre, auteur, raison principale attestée si disponible, nombre de raisons complémentaires, statut d'accès et présence d'indice inféré. Un clic sur la carte visite le livre sélectionné sans accès réseau. Les actions explicites de recherche et actualisation conservent leurs budgets historiques (300 notices locales, 100 résultats distants). La recette visuelle et clavier relève du rôle UX à **1366 et 1600 px**, sans réintroduire de tests de basse résolution.


## Cycle de vie du cache Constellations — #700 (0.3.3)

Le cache déterministe de `ConstellationService.graph_for(identity_id, books,
freshness_token=...)` conserve au maximum **8 graphes** pour des identités
distinctes, par ordre de dernière consultation (*LRU*). Les graphes sont
relativement volumineux lorsque de nombreux livres partagent des facettes
comme la langue ; cette borne limite la rétention mémoire après des
changements répétés de profils.

L'absence de jeton de fraîcheur ne permet toujours aucune réutilisation du
graphe en cache ; chaque jeton nouveau provoque un recalcul. Une identité
différente ne peut jamais reprendre un graphe d'un autre profil. En outre,
`activate()` et `deactivate()` purgent désormais tous les graphes
du singleton : une réactivation avec le même identifiant et le même jeton
mais un contexte Core différent ne réutilise pas d'anciennes données.

Classement, preuves d'arête, promenade, sécurité de téléchargement et
politique de requêtes externes inchangés. Tests hors réseau :
`tests/test_cache_lifecycle_p2.py`.

## Construction indexée du graphe — P2 #700 (0.3.4)

La construction du graphe crée un index inversé sur les facettes **attestées**
(auteurs, séries, thèmes, période, langue, longueur, format, humeur et éditeur).
Une paire de notices sans facette commune n'est plus évaluée par
`relation()` ; les paires candidates sont évaluées **exactement par la même
fonction** qu'auparavant. Scores, ordre des raisons, directions, provenance,
classements et promenades sont inchangés. Il n'y a aucun vecteur, LLM,
lien inféré, accès réseau, nouvelle acquisition ni envoi de métadonnées.

Ce travail élimine la comparaison quadratique pour les catalogues
**clairsemés** (600 livres sans facette commune : zéro appel à `relation()`
contre 179 700 avant), mais n'élimine pas la nature quadratique des graphes
**denses** (ex. tous les livres de langue française) : chaque arête
réellement attestée est encore conservée en mémoire. Un budget d'arêtes
et sa validation UX relèvent d'une décision séparée.

Tests offline `tests/test_facet_graph_index_p2.py` :
parité exacte avec le double parcours naïf, cas clairsemé et dense,
directions de voisinage et protection contre les faux liens entre
types de facettes.

## Refonte du point d'entrée : livre d'abord — #1042 (0.3.5)

Le lecteur ouvre « Explorer les livres proches » dans la fiche d'un livre
de Recherche ou de Bibliothèque. Le livre effectivement choisi, son identité
lorsqu'elle est exposée et ses références documentaires restent le point
de départ. La page présente le livre central et un maximum de **12 voisins**
issus d'arêtes bibliographiques attestées : raisons, disponibilité et source
restent visibles, sans inventer de liens. Un clic sur un voisin recentre
la navigation ; « Livre précédent » / « Livre suivant » parcourent l'historique.

La navigation directe vers la page **ne choisit plus le premier livre** :
elle explique qu'il faut ouvrir une fiche puis l'action contextuelle.
« Retour à la fiche d'origine » emploie la navigation publique
`open_book_detail` avec la référence et l'identité du livre initial.
Si l'hôte ne peut restaurer la fiche, un message non bloquant l'indique.

Les contrôles de modes, proximité, recherche catalogue libre,
sélection arbitraire et **tout parcours départ/arrivée** ont été retirés
de l'interface P2 ; la promenade est une idée P3 #703 et non une action
produit actuelle. Le moteur interne peut subsister mais n'est plus exposé.
Aucun accès réseau, aucune acquisition ou modification de bibliothèque
n'est déclenché en visitant les voisins.

Cette PR apporte l'implémentation Plugin d'un premier parcours minimal ;
la spécification et la recette UX indépendante depuis Recherche ET
Bibliothèque, focus/clavier et vérification visuelle 1366/1600 restent
au rôle UX (#1042/#706). Tests plugins : `tests/test_book_first_p2.py`.


## Flux de bibliothèque borné et sélection isolée — P2 #700 (0.3.6)

La page historique exécutait `tuple(metadata.library_entries(...))` sur la
**totalité** de la bibliothèque avant de n'utiliser que `rows[:300]`.
Cette limite ne bornait ni les lectures, ni la mémoire, ni un itérateur
hostile pouvant ne jamais finir.

Le nouveau chargement applique **`islice(..., 301)` à l'itérateur
source**, puis conserve au plus **300 entrées par profil**. Le 301e
élément sert exclusivement à établir qu'une bibliothèque est plus
grande que le plafond ; dans ce cas le statut indique clairement
**« 300 premières notices locales seulement »**, sans prétendre avoir
exploré l'ensemble du catalogue. La sélection explicite depuis une
fiche livre est ajoutée au graphe indépendamment de la position de
ce livre parmi les premières notices ; aucune navigation ne choisit
arbitrairement le premier livre.

La page ne retient au plus que **8 snapshots d'identités** en mémoire
avec éviction LRU. Le cache graphes du moteur, indépendant, reste
inchangé ; un `refresh=True` ou une notification d'identité invalide
les snapshots. Les callbacks asynchrones continuent à vérifier
génération et identité : une réponse tardive ne peut pas modifier
le cache d'un nouveau profil. Un livre `pending` issu explicitement
de l'identité A n'est pas injecté sous l'identité B ; il peut
être appliqué lorsque A redevient l'identité active.

Tests déterministes sans HTTP ni GTK dans
`tests/test_bounded_identity_loader_p2.py` : générateur infini
(301 éléments lus exactement), seuils 0/1/300, bibliothèque >300,
réutilisation et rafraîchissement, 9 profils et éviction LRU,
graine contextuelle A/B, annulation et callback tardif.
Aucun changement d'algorithme de relation, des raisons, des 12 voisins
affichés ou du contrat Core. **Recette GTK #706** distincte ;
aucun nouveau workflow GitHub Actions.

**Limite fonctionnelle assumée** : seules les 300 premières entrées
proposées par le broker constituent les voisins du cache de la page.
Ce n'est pas une recherche exhaustive du corpus de la bibliothèque.
Une extension paginée explicite requerrait une évolution du contrat
public metadata plutôt qu'un scan illimité.
