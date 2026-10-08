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

## Suite

Ce premier groupe fournit le moteur et le contrat de source. Restent séparés : audit des providers existants, sources spécialisées (BookTrust/équivalents), découverte Internet et UI spécialisée proche de Recherche.
