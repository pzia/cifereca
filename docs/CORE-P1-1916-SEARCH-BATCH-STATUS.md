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
par fournisseur pour les consommateurs du contrat public.

### Tranche Core #1916 — HTTP 429 et Retry-After

Un HTTP 429 **natif** (`httpx.HTTPStatusError`, y compris sous des wrappers
d'exception bornés) active un délai **par session fournisseur, credential et
identité** : au moins 30 secondes, au plus une heure. `Retry-After` est lu
uniquement depuis l'en-tête d'une réponse 429 ; les formats secondes et date
HTTP sont admis. Valeur absente, invalide ou passée : 30 secondes. Aucun corps,
URL signée ou texte d'exception n'est retenu dans la décision.

Pendant le délai, les recherches `search()` et `search_progressive()`
n'envoient pas de nouvelle requête **à cette même session** et utilisent les
avertissements habituels, avec secours public SQLite si attesté. Les autres
fournisseurs poursuivent normalement, les résultats de secours ne deviennent
ni droits, ni acquisition, ni réponse fraîche au cache TTL. Un changement de
session, credential ou identité ne réutilise pas le délai ; la table en mémoire
est bornée à 64 entrées. Pas de `sleep`, de retry implicite ou de persistance.

Un fournisseur qui encapsule le 429 sans conserver le statut HTTP natif
n'atteste pas, pour ce Core, un `Retry-After` : son éventuel quota propre reste
géré dans son adaptateur (ex. Numilog). Les flux de recherche virtuels
(`search_stream`), le repli vers une **route globale indépendante** et la
recette UX intégrée restent distincts, avant clôture complète de #1916.
