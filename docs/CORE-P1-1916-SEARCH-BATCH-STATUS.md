# Core #1916 — statut fermé des lots de recherche fournisseur

`CatalogSearchBatch.result_status` est un champ **optionnel ajouté en fin de
dataclass** ; les constructeurs positionnels historiques continuent à
fonctionner. Les valeurs émises par le Core sont :

| État | Preuve requise | Sens |
| --- | --- | --- |
| `live` | réponse fournisseur sans avertissement/erreur | résultats du travail fournisseur courant (potentiellement aucun) |
| `cached` | réponse du cache de recherche Core (`cached=True`) | résultat d'une recherche antérieure à TTL, **pas** une interrogation réseau |
| `partial` | résultats utilisables **et** erreur/avertissement | ne prouve pas que le catalogue est exhaustif |
| `known_catalog_stale` | notices publiques SQLite attestées après indisponibilité | `search_provenance=known_catalog_stale`, ni droit ni téléchargement |
| `unavailable` | erreur sans résultats exploitables | ne signifie **jamais** « catalogue vide » |
| `fallback_global` | **réservé**, non émis dans ce lot | exige une route globale distincte attestée, jamais un simple retry identique |

Les statuts sont issus d'un helper Core qui utilise uniquement la
classification d'erreur existante, les avertissements déjà épurés et
`search_provenance` des notices de repli Core ; aucun message d'exception
ou de plugin n'entre dans la valeur. La nature précise du refus (401/403) reste
décrite par `error_code` et **n'autorise aucun repli public**.

La GUI conserve actuellement ses avertissements génériques et l'indication
« Résultat connu · hors ligne » par fiche ; ce lot publie la distinction
par fournisseur pour les consommateurs du contrat public. Les opérations de
repli vers une route globale, le diagnostic temporel `Retry-After` et la
recette UX intégrée restent à réaliser avant fermeture de #1916.
