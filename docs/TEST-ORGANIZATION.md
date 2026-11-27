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

### `src/plugins/<plugin>/tests/`

Réservé à :

- parsing fournisseur ;
- session/authentification fournisseur ;
- mapping fournisseur ;
- règles catalogue/acquisition propres ;
- régressions spécifiques au plugin ;
- contrats publics vus depuis ce plugin.

### `simulation/tests/`

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
- `src/plugins/provider-paris/tests/test_sync082_regressions.py` ;
- `tests/test_adept_profile_readiness.py` ;
- `tests/test_adept_artifact_orchestration.py` ;
- `tests/test_kobo_sync_modes.py` ;
- les tests Core ADEPT existants.

L'ancien module `test_sync_082.py` n'est pas présent dans le tree actif et ne doit pas être restauré en bloc.

## Couverture issue de `test_kobo_vault.py`

La couverture utile est répartie entre :

- `src/plugins/provider-kobo/tests/test_vault_contract.py` ;
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

## Rationalisation Core 0.9.21 — capitalisation du 27 novembre 2026

Le plan historique [#103](https://github.com/pzia/cifereca/issues/103), incluant [#102](https://github.com/pzia/cifereca/issues/102), a été livré par les PR [#104](https://github.com/pzia/cifereca/pull/104), [#107](https://github.com/pzia/cifereca/pull/107), [#106](https://github.com/pzia/cifereca/pull/106) et [#109](https://github.com/pzia/cifereca/pull/109). Ne pas restaurer `test_core_0921.py` en bloc : les lots ont été distribués dans les tests spécialisés. La livraison historique n'établit pas à elle seule la couverture actuelle.

Relecture des sources de tests au commit `e8ed298116e57d44645c37cd3fa7ad91e1e9077e` (sans nouvelle exécution des suites) :

- [Inventaire ADEPT](../tests/test_adept_inventory_binding.py) : binding unique attesté et profils explicitement résolus, refus des ambiguïtés et inventaires partiels, conservation du binding et du fichier si l'entrée disparaît, revalidation de l'état returned, aucun appel d'inventaire si artefact absent ou verrou occupé, réutilisation de l'inventaire par combinaison de profils lors du batch.
- [Retour ADEPT](../tests/test_adept_return.py) : les anciens critères de confirmation par refresh d'inventaire, retour non vérifié ou acquisition inconnue sont historiques. Depuis la décision [#4048](https://github.com/pzia/cifereca/issues/4048), la restitution volontaire est retirée : refus `loan_return_removed` avant accès runtime ou mutation, lookup inerte, conservation de l'artefact et de l'état historique. Ne pas réintroduire une restitution pour satisfaire un ancien test.
- [UI ADEPT](../tests/test_adept_ui_contract.py) : réconciliation depuis le détail sur worker ; l'ancienne action de retour et son message de confirmation sont retirés, sans retirer la réconciliation. Aucun succès de retour ne doit être fabriqué.
- [Kobo guard](../tests/test_kobo_backup_guard.py) : les six critères historiques étaient blocage du fichier non lié, permission explicite sans écrasement, exclusion des autres identités et des fichiers absents du blocage, conservation du backup lié complet (`unchanged`), scan préalable. L'ancien exécuteur W2 est désormais retiré ; les tests relus protègent cette absence et l'absence de mutation des candidats locaux hostiles, pas la réactivation de ces six chemins. L'admission et la publication A→B suivent le [contrat canonique](ARCHITECTURE.md) : preuve de présence exacte, consentement et contrôles frais, non-écrasement et séparation des profils ; ne pas restaurer un veto legacy sur la seule présence locale similaire. Les suites de publication atomique/reprise sont la référence pour le parcours canonique.

Les invariants historiques devenus obsolètes sont conservés comme décisions datées, non comme spécification exécutable. Le module historique est absent du tree relu.

## Rationalisation Core 0.9.23 — capitalisation du 27 novembre 2026

Le plan historique [#110](https://github.com/pzia/cifereca/issues/110) a été livré par les PR [#111](https://github.com/pzia/cifereca/pull/111), [#123](https://github.com/pzia/cifereca/pull/123) et [#129](https://github.com/pzia/cifereca/pull/129). Relecture des sources de tests au commit `f31515bedacf7d9ef91a7c7c7ec77a76439044db`, sans nouvelle exécution des suites :

- GTK-C09 — [focus GTK](../tests/test_core0923_gtk_focus.py) : le contrôleur réel, avec témoin GTK injecté, ne demande pas de focus sans racine Gtk.Window ; racine valide → TAB_FORWARD, garde de démontage locale, aucun masquage global des warnings. Cette preuve ne remplace pas une recette GTK réelle.
- IDENT-C08 — [sauvegarde identité](../tests/test_identity_save_ui_contract.py) : rafraîchir sélecteurs/délégations/Accueil uniquement au succès, préserver l'identité active au succès et à l'échec.
- ADE-C07 — [rattachement ADEPT](../tests/test_adept_manual_binding.py) : sélection initiale vide, choix explicite profil/prêt, relecture et ID exact, ancien titre/chemin non probants, conservation de l'artefact et de la liaison précédente, `binding_source=manual_selection`, aucun téléchargement ni retour implicites. Les états expiré/rendu/sans jeton restent informatifs ; restitution volontaire indisponible conformément à #4048.

Le module historique `test_core_0923.py` est absent du tree relu. Le plan de 0.9.23 excluait le choix visuel multi-profils, l'acquisition entièrement perdue, les autres détails d'Artifact sans ledger et la recette GTK réelle : ne pas leur attribuer une validation implicite.

## État historique 0.9.25–0.9.33 à requalifier

Le compte rendu [#112](https://github.com/pzia/cifereca/issues/112) du 7 octobre 2026 indique le triage 0.9.23–0.9.33 terminé (invariants migrés, déjà couverts ou obsolètes, sans restauration en bloc), à l'exception des invariants ADEPT alors explicitement suspendus par l'arbitrage du 4 octobre. Ce bilan historique ne prouve pas la couverture actuelle. Les sources suivantes restent à requalifier dans la documentation au regard des contrats actifs, **sans restauration transitive** :

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
