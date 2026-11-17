# Classification attestée — preuves de tests CI (#5376)

Lecture des sources et assertions sur `main` au 14/11/2026.
Cette revue vérifie **5 fichiers de tests**, dont **1 seule classification
nouvelle** (`test_ci_test_nested_core_consumers.py`). Les quatre autres
figurent déjà dans `ci-accounting.json` : ils ne peuvent être déclarés une
seconde fois, sous peine d'invalider toute la collecte CI. Tous relèvent de
l'architecture `core` et de l'objet `ci_quality` ; ces constats ne prouvent
pas l'exécution des cas paramétrés.

| Fichier | Niveau | Preuve d'exécution/contrat |
|---|---|---|
| `tests/test_ci_test_nested_core_consumers.py` | `contract` | Tests AST synthétiques, import relatif, collision de namespace et vérification textuelle des routes CI ; **pas de suite plugin réellement exécutée** par ce fichier |
| `tests/test_ci_security_mutation_campaign.py` | `integration` | Lance la campagne dans de vrais sous-processus pytest : baseline et mutants sur DNS/peer, révocation de consentement, intégrité ; **HTTPX simulé**, pas de réseau fournisseur |
| `tests/test_ci_test_collection_inventory.py` | `contract` (registre `ci-accounting.json`) | Vérifie le schéma et la classification, avec collecte pytest de vrais items et publication atomique ; collecte ≠ exécution des tests collectés |
| `tests/test_ci_test_execution_inventory.py` | `integration` | Couvre les phases setup/call/teardown et une exécution pytest subprocess réelle sur des tests synthétiques ; **pas** une recette E2E GTK |
| `tests/test_ci_test_execution_aggregate.py` | `contract` | Agrégation de rapports synthétiques : SHA, doublons, replays, conflits de gates et écritures atomiques ; ne déclenche aucune exécution de tests métier |

**Limites :** `test_level` est le niveau principal du **fichier**. Une suite
peut comporter à la fois assertions unitaires et tests d'intégration. Le
manifeste n'atteste ni couverture métier, ni succès d'un HEAD particulier,
ni indépendance de deux gates qui rejouent le même cas.

Validation : `scripts/ci_test_collection_inventory.py` charge tous les
fragments, rejette les doublons et conserve `unclassified` pour les tests
non qualifiés. Aucun test n'est supprimé ou modifié par ce lot.

Contrainte d'intégrité : chaque chemin classifié doit apparaître dans un seul
fragment JSON. Les niveaux des quatre suites existantes demeurent inchangés,
y compris le niveau `contract` de `test_ci_test_collection_inventory.py`.
