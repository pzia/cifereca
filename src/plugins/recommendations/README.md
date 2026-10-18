# Recommandations CIFERECA 0.2.25

Compatible avec **Plugin API >=1.4,<2** (source de vérité : `cifereca-plugin.json`).

## Structure courante

L'entrypoint stable est `recommendations:PLUGIN`. La vue active est aplatie dans
`recommendations.py` et s'exécute via le broker public `tasks`; les anciennes couches
`recommendations_v022/v023/v024` ne font plus partie du chemin d'exécution.

## 0.2.4 — ajustements UX issus de l'E2E

- état transitoire explicite dans la zone liste pendant l'analyse de la bibliothèque ;
- nombre de livres analysés affiché dès qu'il est connu ;
- bouton **Recalculer** conservé avec libellé et tooltip ; les filtres restent actifs ;
- résultats précédents conservés pendant un recalcul du même profil ;
- badge **Provisoire** persistant tant que le worker tourne, avec avertissement que l'ordre peut évoluer ;
- progression du calcul séparée de l'état de la vue filtrée ;
- cache/réseau déplacés dans le tooltip diagnostic de la barre de progression ;
- ordre provisoire stabilisé par clé afin d'éviter les sauts visuels à chaque batch ;
- score renommé **Affinité** et classement final explicitement indiqué comme **ordre diversifié** ;
- libellés fournisseurs utilisés à la place des identifiants techniques ;
- résumé contextuel des filtres (par exemple `30 sur 80 · Gutenberg`) ;
- liste standard `Gtk.ListView/Gio.ListStore` virtualisée : la recommandation E2E pour 200–300 résultats est couverte structurellement.

## 0.2.3 — présentation maître/détail standard

- la liste de recommandations utilise désormais le composant standard virtualisé de Cifereca ;
- sélection d'une proposition : liste conservée à gauche, détail standard à droite ;
- détail : couverture, auteur, score, raisons, informations catalogue et action « Ouvrir la notice » ;
- contrat public `UiMasterDetailHost` / `UiBookMasterDetailView` : aucun import privé de `cifereca.gui` depuis le plugin ;
- la présentation reste pilotée par le Core : les évolutions de `BookListCell`, `MasterDetailPage` et `DetailPanel` bénéficient automatiquement au plugin.

## 0.2.2 — performance et restitution progressive

- recherche catalogue exécutée hors thread GTK ;
- annulation réelle du calcul précédent via `TaskControl` ;
- premiers résultats affichés au fil des `CatalogSearchBatch` ;
- snapshots partiels coalescés toutes les ~180 ms pour éviter une rafale d'updates GTK ;
- rendu de la liste par petits lots : 8 lignes ajoutées par passage idle, effacement par lots de 20 ;
- classement provisoire léger pendant le réseau ; RRF + MMR complets une seule fois à la fin ;
- profil de bibliothèque calculé une seule fois ;
- MMR optimisé : caractéristiques pré-calculées, similarité maximale incrémentale et pool borné ;
- `force=False` conservé : réutilisation du cache Core ;
- `metadata.library_entries(..., scan=False)` : aucun rescan filesystem.

Le Core 0.9.44 conserve par défaut les recherches **21 600 s (6 h)**. La clé de cache inclut requête, fournisseurs, scope, champ, types et préférences de lecture.

### Mesure synthétique

Sur 120 candidats / 60 sorties : environ **8,8 s → 0,076 s** pour le classement MMR sur l'environnement de test utilisé pendant le développement.  
Sur 400 candidats / 120 sorties : la version optimisée termine autour de **0,23 s** ; l'ancienne implémentation dépassait 2 minutes sur le même scénario plus large.

Ces mesures servent à caractériser le hotspot CPU ; elles ne constituent pas un SLA.

## Fonctionnement

Le plugin construit jusqu'à 8 recherches à partir des auteurs, séries et sujets dominants, interroge les catalogues en parallèle via le Core, fusionne les rangs avec RRF, retire les livres déjà présents et applique une diversification MMR.

Pendant la recherche, les recommandations provisoires sont fondées sur le score de contenu uniquement afin de garder le streaming peu coûteux. Le classement final complet remplace ensuite ces résultats.

Aucun accès privé au Core, aucune acquisition, aucun téléchargement.

## Installation

```bash
cifereca-cli plugin install cifereca-recommendations-0.2.25.zip --replace
```

Puis redémarrer Cifereca si une ancienne version du module était déjà chargée.


## Performance UI

Le calcul est exécuté via le broker `tasks`, mais le scoring reste du Python CPU/GIL :
des candidats identiques renvoyés par plusieurs requêtes/catalogues peuvent donc affecter la
réactivité générale s'ils sont rescored inutilement.

Depuis 0.2.7, le scoring de contenu est mémorisé sur une signature des champs réellement
consommés par l'algorithme (auteurs, thèmes, série, titre, description et complétude).
Le cache est partagé entre le classement provisoire et le classement final.

Mesure reproductible : `tests/test_performance_streaming.py::test_repeated_candidates_are_scored_once_across_streaming_and_final_rank`
simule 2 requêtes × 2 catalogues renvoyant le même candidat. Le chemin historique pouvait
appeler `content_score` 8 fois ; le contrat 0.2.7 impose 1 appel. Une occurrence enrichie
avec un contenu différent possède une signature différente et reste rescored, afin de
préserver la qualité du classement.

Les autres protections existantes restent actives : classement provisoire limité à la tête
utile, coalescence des rafraîchissements partiels, MMR borné et rendu GTK virtualisé.


## Diagnostic de performance

Les refresh Recommendations émettent deux lignes de trace expurgées dans le logger `cifereca.plugin.recommendations` :

- `TRACE recommendations performance ...` : `snapshot_ms`, `profile_ms`, `providers_ms`, `catalog_ms`, `rank_ms`, `total_ms` ;
- `TRACE recommendations ui ...` : `apply_ms` et `total_ui_ms`.

Les traces ne contiennent ni identifiant de profil, ni titre de livre, ni requête catalogue. Pour #519, comparer en priorité `snapshot_ms` au `total_ui_ms` sur le profil représentatif, trois fois de suite.


## Snapshot bibliothèque de session

Depuis 0.2.20, Recommendations conserve un snapshot de projection par identité pendant la session. Les recalculs manuels, changements de filtres nécessitant un recalcul et retours sur une identité déjà visitée ne relisent plus systématiquement `metadata.library_entries(..., scan=False)`. Une notification `identity_overview` invalide le cache avant le recalcul coalescé suivant.

La recette `tests/test_snapshot_burst_measurement.py` compare trois recalculs synthétiques de 562 livres : le chemin sans cache effectue 3 lectures complètes, le chemin avec cache 1 seule. Les traces `TRACE recommendations performance` exposent désormais aussi `snapshot_cached=true|false`, sans identifiant ni contenu de livre.


## Rapport de traces installé

Depuis 0.2.25, `trace_report.py` transforme les traces expurgées de plusieurs relances en un résumé directement exploitable pour #519.

Recette sur une installation réelle :

```bash
grep "TRACE recommendations" <fichier.log> | \
  python3 src/plugins/recommendations/trace_report.py -
```

Le rapport donne le nombre de refresh, les snapshots cachés/non cachés et les médianes/min/max de `snapshot_ms`, profil, recherche catalogue, classement, calcul total, application UI et temps UI total. Avec au moins trois relances, il signale explicitement si plusieurs snapshots complets non cachés persistent après l’amorçage.

Pour une recette déterministe volumineuse, le dépôt fournit aussi `simulation/profiles/ux-library.toml` (1 000 EPUB locaux + Kobo Vault + Numilog). Le rapport n’enregistre ni identité, ni titre, ni requête catalogue.


## Durcissement des identifiants — 0.2.26

La déduplication et le filtre « Exclure les possédés » ne comparent plus un sac de valeurs d’identifiants non typées.

Preuves fortes acceptées :

- ISBN-10/ISBN-13 après validation checksum et canonicalisation en ISBN-13 ;
- couple `provider + source_item_id` (ou alias `item_id` / `record_id`) ;
- autres schémas explicitement nommés dans `identifiers`, conservés sous forme namespacée (`doi:…`, `ark:…`, etc.).

Un provider seul, un identifiant source seul ou un champ générique `id` ne sont pas des preuves bibliographiques suffisantes. Deux catalogues partageant la même valeur brute ne peuvent donc plus produire un faux doublon si leurs namespaces diffèrent.

Le repli faible n’est utilisé qu’en absence totale d’identifiant fort et exige **titre + auteur**. Un titre sans auteur ne suffit jamais à déclarer une recommandation déjà possédée.

Le classement, MMR, streaming progressif, cache de snapshot et mode « livre de référence » restent inchangés.


## Reformulation IA strictement optionnelle — #780 (0.2.27)

Le calcul, les scores, le classement, les 3 raisons attestées et les liens bibliographiques
continuent de fonctionner **sans aucun service IA**.

- Option de configuration `llm_explanations`, **désactivée par défaut** : lorsqu'elle est
  activée, un bouton **« Reformuler les raisons (IA) »** est affiché.
- Sélectionner une recommandation **ne déclenche rien**. Seul un clic sur ce bouton
  envoie les trois raisons déjà présentées, bornées à 160 caractères chacune ;
  le plugin n'envoie ni identité, ni titre, ni bibliothèque entière, ni profil.
  Les raisons peuvent néanmoins contenir des noms d'auteurs ou des sujets : le
  service configuré peut être distant, comme l'indique l'infobulle.
- Une unique demande `LLMRequest(max_output_tokens=96)` passe par
  `context.service("llm")` et le broker public `tasks`, hors du thread GTK.
  Aucune dépendance fournisseur, aucune clef ni logging des prompts/réponses.
- La réponse est bornée, étiquetée **« Reformulation générée — à vérifier »**
  dans une section séparée de **« Pourquoi »**. Le modèle, fournisseur,
  l'indication distant/local et une durée mesurée sont visibles. Ces mots
  **ne deviennent jamais une preuve de sujet, auteur, âge ou droit d'accès**.
- Panne, quota, refus, réponse non générée, annulation, changement de livre,
  profil ou recalcul : l'explication facultative est abandonnée et les
  recommandations attestées restent utilisables. Les réponses tardives
  ne peuvent pas écraser l'explication d'un autre livre.

**Limite :** le ticket Core #778 reste ouvert. Sans configuration du
service public `llm` dans le Core, le bouton explique que le service
est indisponible. Aucun backend n'est simulé dans le produit.

## Fiches Recommandations : isolation des URLs publiques — #1599 (0.2.28)

Dans la liste et la fiche détail, une propriété `thumbnail_url` peut
provenir d'une ancienne notice fournisseur ou d'un cache partiellement
malformé. Un accesseur qui lève une exception ne doit pas faire disparaître
une `cover_url` indépendante et publiquement sûre ; la même règle
s'applique à `friendly_url` puis `notice_url`.

Chaque accès à l'un de ces quatre champs est maintenant isolé. Toute valeur
retenue traverse toujours le même filtre strict `_external_book_url`
(origine publique, sans query privée, identifiants, lien de livraison ou
fichier local). Une erreur du champ prioritaire déclenche **uniquement**
le repli vers un autre champ attesté, sans exposer l'exception, son texte,
une URL signée, ni déclencher une nouvelle requête réseau.

Les tests headless couvrent les quatre getter défaillants, repli vers la
notice et couverture alternatives, absence de toute URL publique,
égalité des couvertures en liste/détail et maintien des droits/actions
inchangés. La recette GTK intégrée #1599/#1598 reste distincte.
