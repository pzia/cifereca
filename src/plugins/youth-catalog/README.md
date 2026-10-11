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


## Reformulation thématique IA opt-in — #781 (0.4.16)

Le filtre des âges et les recommandations de lecture **ne consultent jamais l'IA**.
Le cache des notices, les âges éditeur et leur provenance restent immuables.

- `llm_themes=false` par défaut dans la configuration de la page Jeunesse.
  Une fois activée, la commande **« Reformuler les thèmes (IA) »** s'affiche,
  mais reste indisponible sans livre et sans thèmes attestés.
- Le clic explicite est indispensable à chaque demande. Le service public
  `context.service("llm")` peut être **distant** : seules six étiquettes de
  thème déjà dans le cache (96 caractères chacune au plus) sont envoyées ;
  ni identité, ni titre, ni âge, ni ISBN, ni description complète.
- Appel borné `LLMRequest(max_output_tokens=96)` par le broker `tasks`.
  Un texte généré est présenté dans la section distincte
  **« Thèmes reformulés par IA — non attestés »**, avec modèle et provenance.
  **Une réponse qui énonce une tranche d'âge est refusée**, même si le
  modèle ignore cette consigne.
- Les données générées ne deviennent **ni âge source, ni âge inféré, ni
  thème filtrable, ni preuve bibliographique**. Échec, quota, absence de
  service, annulation ou sélection modifiée : le catalogue et ses filtres
  restent identiques, sans requête réseau implicite.
- La configuration/exécution du service `llm` est une dépendance du ticket
  Core #778 (toujours ouvert). Ce plugin ne crée pas de backend LLM.

Tests unitaires hors réseau dans `tests/test_youth_llm_p2.py`.
