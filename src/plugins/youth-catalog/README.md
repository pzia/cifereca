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

Le cache est isolé par identité et par source. Une mise à jour remplace seulement la source ciblée et invalide l’agrégat de l’identité concernée. `search_cached()` ne déclenche **jamais** de réseau ; les collectes distantes passent uniquement par `collect_source()`.

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

Le moteur, BookTrust, la découverte de sources, Open Library et l’UI spécialisée sont séparés en plugins/incréments. Restent notamment d’autres sources jeunesse qualifiées et la recette UX complète.
