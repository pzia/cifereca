# #1916 — pagination locale du secours de recherche publique (contrat Core)

## Pourquoi

Le Core fournit déjà `known_public_fallback(...)` aux recherches sync et
progressive. Cette API historique retourne jusqu'à **50** notices pour une
source publique en panne, sans attester qu'il n'en existe pas d'autres.

Une API **additionnelle** purement locale est disponible :
`known_public_fallback_page(storage, *, provider_id, identity_id, query,
scope, field, content_types, now=None, page=0, page_size=50)`.

Son résultat immuable `KnownPublicFallbackPage` comporte `results`,
`status`, `page`, `page_size`, `matched_count`, `has_more`. Le compteur
représente uniquement les notices correspondantes déjà connues, filtrées pour
**le fournisseur et l'identité en cours**, dans le budget autorisé. **Jamais**
le nombre total de titres du catalogue distant ni la couverture de son index.
`status=known_catalog_stale` est conservé pour les pages valides portant des
notices ; `no_known_results` indique que cette copie locale ne contient pas de
correspondance, **pas** que le fournisseur distant a répondu 200 zéro.
`page_out_of_range` signifie qu'une page supplémentaire n'existe pas dans
le stock connu ; pas une vérification live.

## Bornes / confidentialité

- Lecture SQLite strictement limitée à **10 001** entrées pour le
  couple provider + identité avant le décodage JSON. Plus de 10 000
  enregistrements ⇒ `scope_budget_exceeded`, **aucune page, aucun faux zéro**.
- `page` est un int exact, non négatif ; `page_size` un int exact entre
  **1 et 50**. Offset borné au budget. Paramètre hors limites ⇒
  `invalid_page`, pas de réseau ni d'exception publiée.
- Reprend les contrôles du secours existant : collection explicitement
  publique, datation de miroir ≤90 jours, filtrage type/format/champ/termes,
  aucune source Numilog privée, aucun emprunt/droit/fichier local requalifié,
  aucun credential, lien signé, couverture ni URL d'acquisition.
- `known_public_fallback` est inchangé : il délègue à la **première page
  de 50**, pour ne modifier ni requête distante, ni retry, ni CoreUI existants.
- Un miroir SQLite n'est **pas un snapshot transactionnel au travers de
  plusieurs appels de pages**. Après synchronisation/changement de profil,
  relancer page 0 et les compteurs ; ne pas présenter `matched_count` comme
  atomicité ou exhaustivité de l'éditeur.

## Handoff CoreUI

Si l'UI introduit « résultats connus suivants », réserver ce lien au
seul statut `known_catalog_stale` avec `has_more=True`, en conservant
l'avertissement de recherche en ligne indisponible et sa fraîcheur déclarée.
Ne jamais lancer une autre requête HTTP, activer un achat, remettre des droits,
ni remplacer l'état courant d'une autre génération de recherche.

Reste hors de ce lot : choix d'une route globale réellement distincte en ligne,
cache d'index complet d'un fournisseur, contrôle GTK, preuve terrain et mesure
de latence #1741. Le ticket #1916 demeure **ouvert**.
