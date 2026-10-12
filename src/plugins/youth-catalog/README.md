# Jeunesse

Socle du catalogue spécialisé Jeunesse (#741), pour les lecteurs de moins de 18 ans.

## Tranches d’âge

Le plugin conserve le libellé source et projette une représentation minimale :

- `age_min` ;
- `age_max` optionnel ;
- `age_kind` : `interest`, `reading`, `recommended`, `publisher` ou `inferred` ;
- `source_label` original ;
- source/provenance ;
- confiance entre 0 et 1.

Le parseur accepte notamment `à partir de 8 ans`, `10–12 ans`, `jusqu’à 6 ans`, `9+`, `Interest age 8-10` et `Reading age 7-9`. Le périmètre est borné à 0–17 ans.

## Rapprochement bibliographique

Les âges ne sont fusionnés qu’avec une preuve forte :

1. ISBN-10/13 valide commun ;
2. URL de notice canonique commune ;
3. même provider + identifiant source.

Un titre+auteur concordant reste une **suggestion** et ne transfère pas automatiquement les âges entre éditions.

## Sources

Le contrat structurel `cifereca.youth-source/1` demande :

- `source_info()` avec `source_id` et `display_name` ;
- `search(query, cursor=None, limit=50)`.

Une source peut retourner `items`/ `results`, `next_cursor` et `fresh_for_seconds`. Une panne d’une source est isolée dans le rapport d’ingestion.

Les providers Cifereca existants peuvent aussi alimenter directement l’index via `ingest()` lorsqu’ils exposent une donnée d’âge réellement attestée.

## Cache et performance

Le cache est isolé par identité et par source. Une mise à jour remplace seulement la source ciblée et invalide l’agrégat de l’identité concernée. `search_cached()` et `find_cached()` ne déclenchent **jamais** de réseau ; les collectes distantes passent uniquement par `collect_source()`.

## Lookup local par identifiants

`find_cached()` interroge uniquement l’agrégat déjà en mémoire avec des preuves fortes : ISBN, provider+identifiant source ou URL de notice. Il ne rapproche jamais titre+auteur et borne la réponse. Les enrichissements bibliographiques déjà appliqués restent visibles dans le résultat.

## Notices directes de bibliothèque — socle P1 #959

Le socle fournit `attest_library_notices(book, results)` et
`YouthCatalogPlugin.attested_library_notices(...)` pour projeter **des
résultats déjà obtenus lors d'une recherche locale explicite**. Aucune
recherche HTTP n'est déclenchée par cette projection.

Une notice est émise uniquement si le résultat porte **un ISBN Bookland
valide identique à l'édition Jeunesse**, un fournisseur et un identifiant
de notice bornés, une URL directe publique HTTPS sans identifiants/query,
et `metadata.direct_notice_attested is True` établi par le fournisseur
lorsqu'il a réellement identifié cette URL comme fiche de l'édition.
`friendly_url` seul, une coïncidence titre/auteur ou un EAN non ISBN
ne constituent **pas** une preuve de notice directe.

Chaque fournisseur garde sa provenance et son lien indépendant. Le
statut `catalogue_reference` veut uniquement dire **référencé au
catalogue** : ni exemplaire, ni disponibilité vérifiée, ni réservation,
ni possession ne sont inférés de `owned`, `downloadable` ou d'un texte
libre. Fraîcheur `unknown` tant qu'une preuve vérifiable ne la fournit
pas ; pas de jeton d'identité ni de données de compte dans la projection.
Le composant appelant reste responsable du périmètre de l'identité
active ; le résultat n'est pas conservé par le plugin.

**Reste à faire #959 :** adapter les fournisseurs compatibles pour qu'ils
attestent réellement leurs liens et leurs ISBN, puis intégrer la
présentation, l'expiration du cache par identité, les vrais statuts
d'exemplaires/stock et la recette GTK. Ces faits ne peuvent pas être
fabriqués par le socle.

## Filtres locaux

Recherche texte, âge, type d’âge, source, langue, format, fiction/documentaire et thèmes sont composables sur le cache local. Les âges inférés peuvent être exclus.

## Page Jeunesse

La page `ui.page.youth` reprend le modèle de Recherche avec des filtres locaux dédiés :

- âge exact 0–17 ans ;
- type d’âge (recommandé, intérêt, lecture, éditeur, inféré) ;
- source, langue, format, type et thèmes ;
- recherche texte locale.

Les âges inférés sont exclus par défaut. Ouvrir la page ou modifier un filtre ne déclenche jamais de réseau.

Une collecte distante exige de sélectionner une source puis de cliquer **Collecter cette source**. Les sources exposant un `browse()` âge-aware peuvent recevoir âge et thèmes ; sinon le contrat standard `search()` est utilisé. **Plus de résultats** reprend seulement le curseur explicite de cette source.

La liste/détail standard affiche la tranche d’âge, son type, le libellé d’origine, la source et la confiance. Une notice publique attestée reste ouvrable dans le navigateur et la navigation vers la fiche Bibliothèque utilise uniquement le contrat public du Core.

## Enrichissement bibliographique explicite

Une notice Jeunesse sélectionnée peut être enrichie via les services `youth.bibliography` activés.

- l’action **Enrichir la bibliographie** exige un ISBN valide ;
- aucun rapprochement automatique titre+auteur n’est effectué ;
- l’ISBN-10 est canonicalisé en ISBN-13 avant comparaison ;
- seuls des résultats déclarés `bibliographic_only=true`, `age_evidence=none` et sans tranche d’âge sont acceptés ;
- une réponse ambiguë n’est pas appliquée ;
- auteurs, langue, sujets, éditeurs, année et couverture peuvent compléter la notice ;
- la provenance des âges reste inchangée : une source bibliographique ne devient jamais une source d’âge ;
- le rendu et les filtres restent cache-only ; l’appel distant n’a lieu que sur le bouton explicite.

## Suite

Le moteur, la découverte de sources, Open Library et l’UI spécialisée sont séparés en plugins/incréments. Restent notamment d’autres sources jeunesse qualifiées et la recette UX complète.

### Filtre d'âge strict (0.4.1)

Les recommandations françaises passent par `cifereca.plugin_api.youth_ages`.
Les libellés anglais complets, par exemple `Interest age 8-10` ou
`Reading age 7 to 9`, sont normalisés sans extraire de nombres dans une
phrase. `8 ans`, une description libre, un âge approximatif ou une
correspondance partielle restent inconnus. Le libellé original et la
provenance sont conservés.

Les âges inférés sont exclus du filtre par défaut ; `include_inferred=True`
constitue un opt-in explicite. Les notices sans âge attesté restent dans le
cache mais ne satisfont pas un filtre d'âge. Aucun accès réseau n'est ajouté.



## Qualification HTML éditeurs — Rageot #952 / Bayard #950

La commande ci-dessous est **manuelle**, facultative et ne s'exécute ni à
l'installation ni pendant les tests. Elle enregistre une **réponse brute non
nettoyée**, uniquement pour les URL officielles explicitement autorisées dans
`tools/capture_publisher_html.py` ; HTTP 200, URL finale exacte, `text/html`,
taille plafonnée, refus des challenges et des redirections.

```bash
cd src/plugins/youth-catalog
python tools/capture_publisher_html.py rageot-catalog --output-dir /tmp/cifereca-sources
python tools/capture_publisher_html.py rageot-page-2 --output-dir /tmp/cifereca-sources
python tools/capture_publisher_html.py rageot-age-8 --output-dir /tmp/cifereca-sources
python tools/capture_publisher_html.py rageot-book --output-dir /tmp/cifereca-sources
python tools/capture_publisher_html.py bayard-book --output-dir /tmp/cifereca-sources
```

Chaque capture produit `<label>.raw.html` (permissions privées) et
`<label>.capture.json` (URL attestée et SHA-256 des octets reçus).
**Ne jamais ajouter les fichiers `raw.html` au dépôt sans nettoyage et
revue manuelle** : supprimer scripts, traceurs, paramètres/IDs de session,
données personnelles et éléments non nécessaires ; conserver uniquement
une fixture minimale du DOM d'origine et sa provenance. Une réponse
inaccessible ou redirigée n'est pas une preuve de DOM. Avant toute
implémentation d'adaptateur, comparer également au moins deux fiches,
qualifier les âges *propres à l'édition* et rejeter les produits hors livre.
Aucune supposition de DOM commun avec Milan.


## Recherche fédérée Jeunesse au clic — P2 #1028 (0.4.19)

Un bouton autonome **« Rechercher dans toutes les sources Jeunesse »**
interroge, **uniquement au clic**, les sources actives exposant `browse`
ou `search`. Pas de HTTP pendant la saisie, l'ouverture, le choix du profil
ou des filtres. Le bouton de collecte d'une source et sa pagination sont
inchangés. L'opération utilise `context.broker("tasks")` et deux workers
concurrents au maximum.

**Budgets de protection :** au plus 8 sources, 2 pages/source,
50 notices/page, 2 connexions simultanées. Une page dépassant la limite,
une réponse mal formée, une boucle de curseur ou un fournisseur défaillant
rejette sa source sans effacer son cache valide ; les autres sources
continuent. Les exceptions fournisseurs ne sont jamais réaffichées.
L'état donne les sources interrogées, indisponibles/ignorées et les
notices admises. Les pages précédentes ne sont pas collectées sans clic ;
la deuxième page, quand attestée, est récupérée dans la même demande
explicitement bornée. Aucun lien d'acquisition n'est appelé.

Les réponses sont ingérées **progressivement sur le thread GTK** dans
l'identité active, après contrôle de génération/recherche/profil et du
signal d'annulation. Les workers ne mutent jamais le cache directement.
La déduplication bibliographique et les preuves d'âge sont celles du
cache Jeunesse existant ; aucune inférence ou rapprochement par simple
titre ne provient de la fédération. Un seuil `minimum_age` publié
n'est **pas converti en âge exact** dans les API amont hétérogènes :
le filtrage réellement disponible est réappliqué localement après
ingestion. La source peut ne pas exposer tous les champs recherchés.

**Limites :** un lot source invalide n'applique pas de résultats partiels
de cette source ; source non compatible/inactive ignorée sans bloquer
les autres. La pertinence des textes et les explications des matchs sont
suivies dans #1032/#1023 (Core), la simplification des filtres #1029
est dans une PR distincte. Les scénarios UX GTK 1366/1600 et le cumul
des deux PR Jeunesse devront être validés par le merger/UX.

Tests hors réseau : `tests/test_federated_search_p2.py`.

## Liens de notices et de couvertures (#959)

Les liens affichés dans la liste et le détail passent par `public_url()` :
HTTP(S) public seulement, sans identifiants intégrés, fragment de session,
paramètres de signature/authentification, hôte local/privé, port arbitraire
ni URL malformée ou surdimensionnée. Les paramètres de présentation
ordinaires (par exemple `width=180`) sont préservés.

Si `friendly_url` est refusée, une `notice_url` sûre et indépendante peut
encore être affichée. Un lien accepté n'atteste **ni la possession d'une
édition, ni un exemplaire, ni une disponibilité** ; la recherche bibliothèque
par ISBN reste une action explicite distincte (#959).
