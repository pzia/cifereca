# Organisation des tests CIFERECA

État documentaire : 7 octobre 2026.

## Règle de placement

Le test est placé selon **ce qu'il affirme**, pas selon les dépendances qu'il utilise.

### `tests/`

Réservé à :

- comportement Core ;
- contrats transversaux ;
- orchestration générique ;
- interactions entre plusieurs sous-systèmes Core ;
- invariants publics qui ne sont pas propres à un provider.

### `plugins/<plugin>/tests/`

Réservé à :

- parsing fournisseur ;
- session/authentification fournisseur ;
- mapping fournisseur ;
- règles catalogue/acquisition propres ;
- régressions spécifiques au plugin ;
- contrats publics vus depuis ce plugin.

### `gen-dataset/tests/`

Valide le banc de simulation **actuel** : faux services, profils et orchestration de dataset. Ces tests ne remplacent ni les tests Core ni les tests fournisseur.

### `environnements/`

Les self-tests valident les recettes machine/runtime : GTK, Xvfb, D-Bus, bootstrap, capture. Ils ne certifient pas à eux seuls le comportement fonctionnel de Cifereca.

## Règles de rationalisation historique

- ne jamais restaurer en bloc les anciens tests `_bundled` ;
- migrer les invariants historiques méthode par méthode ;
- ne pas créer de chaînes où de nouveaux tests importent d'anciens tests comme dépendances ;
- lorsqu'un ancien test mélange Core et fournisseur, répartir sa couverture selon la frontière actuelle ;
- supprimer/restaurer un test uniquement après avoir identifié l'invariant qu'il protège.

## Historique clôturé : ne pas restaurer en bloc

Les sources historiques suivantes sont considérées comme rationalisées ou remplacées. Elles ne constituent pas une liste de fichiers à réintroduire :

- `test_095_consolidation.py` historique ;
- `test_sync_082.py` ;
- `test_kobo_vault.py` ;
- anciens scénarios Gallica de `test_gallica_plugin.py` ;
- anciens scénarios updater de `test_update.py`.

Le tree courant contient encore `tests/test_095_consolidation.py` sous forme de couverture Core active issue de la restauration/rationalisation 0.9.2. Cette présence ne justifie pas de restaurer une ancienne variante du module en bloc.

## Couverture issue de `test_sync_082.py`

La couverture utile est désormais répartie entre :

- `tests/test_sync_state_contract.py` ;
- `plugins/provider-paris/tests/test_sync082_regressions.py` ;
- `tests/test_adept_profile_readiness.py` ;
- `tests/test_adept_artifact_orchestration.py` ;
- `tests/test_kobo_sync_modes.py` ;
- les tests Core ADEPT existants.

L'ancien module `test_sync_082.py` n'est pas présent dans le tree actif et ne doit pas être restauré en bloc.

## Couverture issue de `test_kobo_vault.py`

La couverture utile est répartie entre :

- `plugins/provider-kobo/tests/test_vault_contract.py` ;
- `tests/test_kobo_executor_retries.py` ;
- `tests/test_kobo_backup_fault_isolation.py` ;
- `tests/test_kobo_backup_observability.py` ;
- `tests/test_kobo_backup_reconcile_prune.py` ;
- `tests/test_kobo_executor_policy.py` ;
- `tests/test_kobo_executor_kdrm.py` ;
- `tests/test_kobo_archive_metadata.py` ;
- `tests/test_kobo_adept_state.py` ;
- `tests/test_kobo_vault_closure.py`.

L'ancien `test_kobo_vault.py` n'est pas présent dans le tree actif.

## Chantier historique encore à analyser

La chaîne historique suivante reste un chantier de triage, **sans restauration transitive** :

- `test_core_0921.py` ;
- `test_core_0923.py` ;
- `test_core_0925.py` ;
- `test_core_0927.py` ;
- `test_core_0929.py` ;
- `test_core_0931.py` ;
- `test_core_0933.py`.

Ces fichiers ne sont pas présents dans le tree courant au moment de cette documentation. Ils doivent être étudiés depuis leur source/historique, méthode par méthode, puis chaque invariant doit être classé Core, plugin, simulation ou obsolète.

## Validation minimale d'une migration de test

Pour chaque invariant historique retenu :

1. identifier le comportement encore supporté ;
2. choisir le propriétaire actuel (Core ou plugin) ;
3. écrire/restaurer le test dans le bon emplacement sans importer le test historique ;
4. lancer le test ciblé ;
5. lancer la suite Core ou plugin pertinente ;
6. documenter explicitement les assertions abandonnées comme historiques si elles ne correspondent plus à l'architecture actuelle.
