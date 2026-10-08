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
