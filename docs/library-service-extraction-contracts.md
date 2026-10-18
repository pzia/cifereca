# Contrats de LibraryService avant extraction — lot 0 (#1116)

La façade `from cifereca.services import LibraryService` reste le point d'entrée.
Cet inventaire couvre les 196 méthodes de la baseline. Le fixture
`tests/fixtures/library_service_facade.json` gèle leurs signatures et types de
descripteurs, y compris les méthodes privées appelées non liées par les tests.
Une extraction peut les déléguer ou les hériter ; le test ne dépend pas du
fichier qui porte physiquement leur corps. Une nouvelle méthode est permise.
Un changement intentionnel d'API exige une revue explicite du fixture.

Le JSON associé donne pour chaque méthode les attributs `self`, imports globaux
et callsites candidats (recherche statique, pas preuve de dispatch dynamique).
Les compteurs ci-dessous renvoient à cette liste complète. Les destinations
sont des propositions : réutiliser les modules déjà présents et garder les
contrats avant de créer un module supplémentaire.

## Frontières et ordre

Propriétaire Core pour chaque lot : #1117 sessions/collections ; #1118 recherche ;
#1119 catalogue/historique/couvertures/materialization ; #1120 acquisition/retour ;
#1121 backup Kobo ; #1122 opening-sync/dashboard. Garder configuration/identités
et l'assemblage dans la façade. Chaque extraction reste indépendante et
réversible. Les orchestrateurs déjà séparés (provider_stream, provider_search,
materialization, return_adept_orchestrator, sync_state) sont réutilisés.

Ne déplacer les locks qu'avec une injection depuis la façade : les tests patchent
`cifereca.services.InterProcessLock` et `acquisition_lock_file`, et les méthodes
appelées non liées reçoivent parfois un SimpleNamespace. Un import capturé dans
un nouveau module casserait ce contrat. Conserver le même chemin de verrou
interprocessus entre acquisitions automatique/manuelle, sync et retour.
Le thread principal reçoit seulement des projections via TaskRunner/GUI ; aucun
import GTK/PyGObject dans ces modules. Les subscriptions de collections et
workers de recherche doivent être annulés avant fermeture des sessions.
Ne déplacer aucun contrat public plugin vers un module privé.

Sens des dépendances : façade → helpers Core → contrats/models/storage.
Un helper ne doit pas réimporter LibraryService ; fournir un protocole ou des
callbacks injectés pour ses besoins de façade. Les imports différés des
adapters/brokers gardent leur frontière et leur cycle actuel. Le JSON est un
inventaire de référence à relire lors du lot concerné, pas un graphe exhaustif
des imports dynamiques.

Le lot sessions/collections (#1117) utilise `core/provider_sessions.py` :
coordinateur interne sans état supplémentaire, recevant explicitement la
façade qui possède registre, sessions, verrous et abonnements. Les 19 points
d'entrée de façade délèguent en conservant signatures et descripteurs.
Executor de recherche et logger TRACE sont injectés à chaque appel depuis
`services`, donc les patchpoints existants restent actifs. Aucun scheduler
ni cache parallèle ; `provider_collection_cache` garde son rôle.

Le lot recherche (#1118) délègue 19 méthodes à `core/search_orchestration.py`.
Il réutilise ranking/recherche et provider_stream existants. Executor, logger
et type de façade (pour les anciens appels statiques non liés) sont injectés
explicitement ; pas de réimport inverse. Le cache TTL, backpressure et l'ordre
des callbacks restent portés par le corps de l'orchestration déplacée.

Le lot bibliothèque (#1119) distingue `core/library_projection.py` (11 lectures
de bibliothèque/historique/couvertures) et `core/library_mutation.py` (4 actions
explicites de matérialisation, scan, indexation et ledger). Les contrats AST de
placement/source/couverture/date inspectent désormais leur implémentation ;
leurs assertions sont conservées et complétées par des scénarios SQLite/fichiers
réels (acquisition complète et exacte, copie absente/dupliquée, pas de scan/sync).

## Preuves de comportement à conserver

| Invariant | Suite de caractérisation | Effet observé |
|---|---|---|
| Identité UI différente | test_core_632_service_identity_snapshots.py, test_core_library_read_identity_scope.py | scopes explicites, pas fallback UI |
| Recherche / annulation / gap | test_core_0968_provider_stream.py | annulation, cache, reset sûr, résultat borné |
| Deux connexions Paris | test_paris_borrower_adept_binding.py, test_adept_borrower_identity_guard.py | grant par connexion, conflit avant mutation |
| Lock ADEPT commun | test_adept_borrower_identity_guard.py, test_core_0984_adept_first_paris_reconcile.py | busy ne lance ni fulfillment ni second retour |
| Restitution sans second POST | test_core_0984_adept_first_paris_reconcile.py, test_core_0982_loan_return_reconciliation.py | retour Adobe confirmé puis Paris lu sans cache |
| Échec persistant / reprise explicite | test_core_0977_adept_retry_guard.py, test_core_0913.py | deux ouvertures sans retry automatique, ACSM conservé |
| Sync partielle et reprise | test_core_0978_source_state.py, test_sync_state_contract.py | dernier succès préservé, aucune réussite inventée |
| Backup / ledger | test_kobo_backup_guard.py, test_kobo_sync_modes.py | artefact valide réutilisé, lock et fraîcheur conservés |

Les payloads, codes AttentionRequired/ProviderConfigurationError, états SQLite,
calls réseau et fichiers sont affirmés dans ces suites comportementales : ne
les remplacer ni par le seul snapshot de signatures ni par des assertions AST.
Import headless et API façade font l'objet de tests supplémentaires dédiés.
Aucune migration, opération réseau réelle ou changement métier dans ce lot.

## Matrice complète

| Méthode | Lot | Destination proposée | self.* | Callsites candidats | Risque import/lock/thread |
|---|---|---|---:|---:|---|
| `__init__` | #1116 | `services.py + identities.py` | 42 | 71 | Lock, RLock |
| `_identity_directory_engine` | #1116 | `services.py + identities.py` | 3 | 0 | — |
| `_recover_identity_directory_migration` | #1116 | `services.py + identities.py` | 3 | 0 | InterProcessLock, acquisition_lock_file |
| `save_diagnostic_preferences` | #1116 | `services.py + identities.py` | 2 | 2 | — |
| `can_download_loan` | #1116 | `services.py + identities.py` | 1 | 2 | — |
| `save_configuration` | #1116 | `services.py + identities.py` | 8 | 4 | InterProcessLock, acquisition_lock_file |
| `change_identity_directory` | #1116 | `services.py + identities.py` | 3 | 3 | InterProcessLock, acquisition_lock_file |
| `_sync_identity_configuration` | #1116 | `services.py + identities.py` | 4 | 0 | — |
| `identity_by_id` | #1116 | `services.py + identities.py` | 1 | 4 | — |
| `identity_for_connection` | #1116 | `services.py + identities.py` | 4 | 5 | — |
| `accounts_for_identity` | #1116 | `services.py + identities.py` | 2 | 2 | — |
| `sync_source_id` | #1116 | `services.py + identities.py` | 0 | 1 | — |
| `sync_source_enabled` | #1116 | `services.py + identities.py` | 2 | 1 | — |
| `select_identity` | #1116 | `services.py + identities.py` | 7 | 6 | — |
| `library_identity_provider_dir` | #1116 | `services.py + identities.py` | 3 | 4 | — |
| `_migrate_legacy_flat_kobo_files` | #1116 | `services.py + identities.py` | 4 | 0 | — |
| `adept_profile_choices` | #1116 | `services.py + identities.py` | 3 | 1 | — |
| `resolve_adept_profile_for_identity` | #1116 | `services.py + identities.py` | 6 | 0 | — |
| `resolve_adept_profile` | #1116 | `services.py + identities.py` | 2 | 1 | — |
| `_local_account` | #1116 | `services.py + identities.py` | 1 | 0 | — |
| `_session_key` | #1116 | `services.py + identities.py` | 0 | 12 | — |
| `active_session` | #1116 | `services.py + identities.py` | 2 | 0 | — |
| `attach_plugin_manager` | #1117 | `core/provider_sessions.py` | 10 | 6 | — |
| `_unbind_virtual_subscriptions` | #1117 | `core/provider_sessions.py` | 2 | 0 | — |
| `_sync_virtual_subscriptions` | #1117 | `core/provider_sessions.py` | 3 | 0 | — |
| `_queue_virtual_collection_refresh` | #1117 | `core/provider_sessions.py` | 5 | 0 | — |
| `_drain_virtual_collection_events` | #1117 | `core/provider_sessions.py` | 6 | 0 | — |
| `subscribe_virtual_sources` | #1117 | `core/provider_sessions.py` | 2 | 1 | — |
| `_connect_changed_virtual_provider` | #1117 | `core/provider_sessions.py` | 7 | 0 | — |
| `refresh_virtual_collections` | #1117 | `core/provider_sessions.py` | 13 | 2 | — |
| `plugin_manager` | #1117 | `core/provider_sessions.py` | 1 | 4 | — |
| `_provider_plugin_status` | #1117 | `core/provider_sessions.py` | 2 | 1 | — |
| `_provider_enabled_for_identity` | #1117 | `core/provider_sessions.py` | 5 | 1 | — |
| `provider_descriptors` | #1117 | `core/provider_sessions.py` | 1 | 4 | — |
| `provider_diagnostic_state` | #1117 | `core/provider_sessions.py` | 4 | 2 | — |
| `close` | #1117 | `core/provider_sessions.py` | 7 | 145 | — |
| `disconnect_configured` | #1117 | `core/provider_sessions.py` | 10 | 0 | — |
| `connect_configured` | #1117 | `core/provider_sessions.py` | 20 | 8 | — |
| `login` | #1117 | `core/provider_sessions.py` | 0 | 5 | — |
| `select_account` | #1117 | `core/provider_sessions.py` | 8 | 3 | — |
| `_session_for_account` | #1117 | `core/provider_sessions.py` | 3 | 0 | — |
| `_session_for_source` | #1117 | `core/provider_sessions.py` | 3 | 5 | — |
| `_key` | #1117 | `core/provider_sessions.py` | 1 | 2 | — |
| `_loans_for_account` | #1117 | `core/provider_sessions.py` | 4 | 4 | — |
| `refresh_loans` | #1117 | `core/provider_sessions.py` | 5 | 3 | — |
| `refresh_identity_loans` | #1117 | `core/provider_sessions.py` | 6 | 2 | — |
| `public_catalogue_target` | #1117 | `core/provider_sessions.py` | 2 | 2 | — |
| `public_catalogue_url` | #1117 | `core/provider_sessions.py` | 0 | 2 | — |
| `_provider_loan_active_for_acquisition` | #1117 | `core/provider_sessions.py` | 2 | 0 | — |
| `refresh_adept_inventory` | #1117 | `core/provider_sessions.py` | 2 | 1 | InterProcessLock, acquisition_lock_file |
| `refresh_history` | #1117 | `core/provider_sessions.py` | 8 | 4 | — |
| `_history_fold` | #1119 | `core/library_history.py + core/covers.py` | 0 | 0 | — |
| `_history_cover_score` | #1119 | `core/library_history.py + core/covers.py` | 0 | 0 | — |
| `_history_search_queries` | #1119 | `core/library_history.py + core/covers.py` | 0 | 0 | — |
| `resolve_history_thumbnail` | #1119 | `core/library_history.py + core/covers.py` | 6 | 1 | — |
| `refresh_bookings` | #1119 | `core/library_history.py + core/covers.py` | 6 | 3 | — |
| `provider_display_name` | #1119 | `core/library_history.py + core/covers.py` | 1 | 2 | — |
| `_search_query_variants` | #1118 | `core/provider_search.py + core/provider_stream.py` | 0 | 1 | — |
| `_search_relevance` | #1118 | `core/provider_search.py + core/provider_stream.py` | 0 | 0 | — |
| `_normalized_language_values` | #1118 | `core/provider_search.py + core/provider_stream.py` | 0 | 0 | — |
| `_search_preference_score` | #1118 | `core/provider_search.py + core/provider_stream.py` | 2 | 1 | — |
| `availability_display_label` | #1118 | `core/provider_search.py + core/provider_stream.py` | 2 | 2 | — |
| `searchable_providers` | #1118 | `core/provider_search.py + core/provider_stream.py` | 2 | 7 | — |
| `_session_search_capabilities` | #1118 | `core/provider_search.py + core/provider_stream.py` | 0 | 0 | — |
| `provider_search_capabilities` | #1118 | `core/provider_search.py + core/provider_stream.py` | 3 | 2 | — |
| `_search_contract_warning` | #1118 | `core/provider_search.py + core/provider_stream.py` | 0 | 1 | — |
| `_field_match` | #1118 | `core/provider_search.py + core/provider_stream.py` | 0 | 0 | — |
| `_without_redundant_merged` | #1118 | `core/provider_search.py + core/provider_stream.py` | 2 | 1 | — |
| `search` | #1118 | `core/provider_search.py + core/provider_stream.py` | 15 | 86 | — |
| `_search_provider_session` | #1118 | `core/provider_search.py + core/provider_stream.py` | 4 | 1 | — |
| `_search_virtual_stream_session` | #1118 | `core/provider_search.py + core/provider_stream.py` | 2 | 0 | — |
| `search_progressive` | #1118 | `core/provider_search.py + core/provider_stream.py` | 19 | 9 | Queue, wait |
| `search_local_library` | #1118 | `core/provider_search.py + core/provider_stream.py` | 2 | 0 | — |
| `search_known_catalog` | #1118 | `core/provider_search.py + core/provider_stream.py` | 3 | 3 | — |
| `filter_loaded_search_results` | #1118 | `core/provider_search.py + core/provider_stream.py` | 0 | 0 | — |
| `_mirror_remote_results` | #1119 | `core/library_materialization.py + materialization.py` | 2 | 3 | — |
| `mirror_personal_libraries` | #1119 | `core/library_materialization.py + materialization.py` | 6 | 1 | — |
| `_acquire_personal_library_item` | #1119 | `core/library_materialization.py + materialization.py` | 3 | 0 | — |
| `recent_acquisitions` | #1119 | `core/library_materialization.py + materialization.py` | 4 | 3 | — |
| `local_book_path` | #1119 | `core/library_materialization.py + materialization.py` | 3 | 3 | — |
| `local_placement_for_source` | #1119 | `core/library_materialization.py + materialization.py` | 2 | 3 | — |
| `local_placements_for_sources` | #1119 | `core/library_materialization.py + materialization.py` | 2 | 2 | — |
| `materialize_to_identity` | #1119 | `core/library_materialization.py + materialization.py` | 5 | 4 | — |
| `holdings` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 3 | 7 | — |
| `renew` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 3 | 2 | — |
| `_return_loan_identity` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 3 | 0 | — |
| `loan_return_state` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 1 | 2 | — |
| `verify_return_digital` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 2 | 1 | — |
| `verify_paris_return_inventory` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 1 | 0 | — |
| `_adobe_return_inventory_confirmed` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 4 | 0 | — |
| `_return_operation_lock_file` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 1 | acquisition_lock_file |
| `return_loan_prefer_adept` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 3 | 2 | InterProcessLock |
| `_return_loan_prefer_adept_guarded` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 7 | 0 | — |
| `verify_loan_return` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 3 | 1 | — |
| `return_digital` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 2 | 3 | InterProcessLock |
| `_return_digital_guarded` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 7 | 0 | — |
| `pnb_preferences` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 2 | 3 | — |
| `content_type_for` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 1 | 3 | — |
| `default_downloads_dir` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 1 | 1 | — |
| `_kobo_adapter` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 1 | 2 | — |
| `_required_plugin_service` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 1 | 0 | — |
| `_http_plain_transport_proof` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 0 | — |
| `execute_acquisition_request` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 4 | 19 | — |
| `_originating_adept_profile_id` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 4 | 1 | — |
| `_adept_return_capability` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 1 | 0 | — |
| `_adept_acquisition_path` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 1 | 0 | — |
| `_adept_inventory_candidates` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 1 | 0 | — |
| `_reconcile_adept_acquisition` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 9 | 0 | — |
| `adept_loan_choices` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 7 | 3 | — |
| `attach_adept_loan` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 9 | 3 | InterProcessLock, acquisition_lock_file |
| `reconcile_adept_acquisition` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 2 | 3 | InterProcessLock, acquisition_lock_file |
| `_reconcile_identity_adept_inventory` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 2 | 3 | — |
| `adept_return_for_artifact` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 2 | 2 | — |
| `return_adept_acquisition` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 1 | 5 | InterProcessLock, acquisition_lock_file |
| `_return_adept_acquisition` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 8 | 0 | — |
| `execute_drm_mutation` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 2 | 1 | — |
| `_import_acsm_impl` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 9 | 0 | — |
| `import_acsm` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 4 | 5 | — |
| `runtime_diagnostic` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 1 | 6 | — |
| `kobo_adept_state` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 3 | 4 | — |
| `kobo_profiles` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 1 | 4 | — |
| `_adept_profile_ref` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 0 | — |
| `_adept_profile_name` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 0 | — |
| `_adept_profile_row` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 1 | 0 | — |
| `_adept_profile_cli_name` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 2 | 0 | — |
| `can_direct_download` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 3 | 2 | — |
| `_kobo_adept_format` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 1 | — |
| `_kobo_candidate_media_format` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 1 | — |
| `_kobo_candidate_drm` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 1 | — |
| `_kobo_candidate_handling` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 1 | — |
| `_kobo_candidate_profile` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 1 | — |
| `_kobo_candidate_source` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 1 | — |
| `_kobo_candidate_requires_authorization` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 1 | — |
| `_kobo_acsm_candidate` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 2 | — |
| `_kobo_candidate` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 2 | — |
| `_kobo_delegated_candidate` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 1 | — |
| `_kobo_canonical_drm` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 0 | — |
| `_kobo_url_format` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 1 | — |
| `_kobo_delegated_request` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 1 | — |
| `_kobo_kdrm_context` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 1 | — |
| `_observed_backup_artifact` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 2 | — |
| `_kobo_artifact_plausible` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 0 | — |
| `_kobo_record_candidate_paths` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 2 | 0 | — |
| `_kobo_destination` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 2 | 1 | — |
| `_execute_kobo_acquisition` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 0 | 5 | — |
| `_acquire_direct_item_impl` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 7 | 0 | — |
| `acquire_direct_item` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 4 | 11 | — |
| `obtain_search_item` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 3 | 2 | — |
| `prepare_direct_download` | #1120 | `core/loan_acquisition.py + return_adept_orchestrator.py` | 2 | 0 | — |
| `library_dir` | #1119 | `core/library_catalog.py` | 1 | 1 | — |
| `catalog_scan` | #1119 | `core/library_catalog.py` | 3 | 15 | — |
| `catalog_entries` | #1119 | `core/library_catalog.py` | 2 | 14 | — |
| `catalog_index_path` | #1119 | `core/library_catalog.py` | 2 | 4 | — |
| `library_entries` | #1119 | `core/library_catalog.py` | 5 | 13 | — |
| `_persist_active_loans` | #1120 | `core/loan_acquisition.py` | 1 | 8 | — |
| `_loan_destination_name` | #1120 | `core/loan_acquisition.py` | 0 | 1 | — |
| `_provider_attention_code` | #1120 | `core/loan_acquisition.py` | 0 | 0 | — |
| `_diagnose_adept_binding` | #1120 | `core/loan_acquisition.py` | 1 | 0 | — |
| `adept_return_status_for_loan` | #1120 | `core/loan_acquisition.py` | 7 | 3 | — |
| `reacquire_digital_loan` | #1120 | `core/loan_acquisition.py` | 7 | 3 | InterProcessLock, acquisition_lock_file |
| `_borrower_identity_conflict` | #1120 | `core/loan_acquisition.py` | 0 | 1 | — |
| `bind_paris_borrower_adept` | #1120 | `core/loan_acquisition.py` | 3 | 5 | — |
| `_paris_adept_identity_for_loan` | #1120 | `core/loan_acquisition.py` | 4 | 1 | — |
| `_cross_identity_loan_conflict` | #1120 | `core/loan_acquisition.py` | 0 | 1 | — |
| `_acquire_active_loan_automatically` | #1120 | `core/loan_acquisition.py` | 1 | 11 | InterProcessLock, acquisition_lock_file |
| `_acquire_active_loan_automatically_guarded` | #1120 | `core/loan_acquisition.py` | 19 | 3 | — |
| `_synchronize_account_source_impl` | #1122 | `core/opening_sync.py + sync_state.py` | 5 | 0 | — |
| `synchronize_account_source` | #1122 | `core/opening_sync.py + sync_state.py` | 2 | 1 | — |
| `preview_library_filename` | #1122 | `core/opening_sync.py + sync_state.py` | 2 | 2 | — |
| `opening_sync` | #1122 | `core/opening_sync.py + sync_state.py` | 2 | 8 | InterProcessLock, acquisition_lock_file |
| `_opening_sync_locked` | #1122 | `core/opening_sync.py + sync_state.py` | 25 | 1 | — |
| `_record_action` | #1122 | `core/opening_sync.py + sync_state.py` | 2 | 2 | — |
| `action_statistics` | #1122 | `core/opening_sync.py + sync_state.py` | 1 | 0 | — |
| `_record_loan_statistics` | #1122 | `core/opening_sync.py + sync_state.py` | 1 | 1 | — |
| `identity_statistics` | #1122 | `core/opening_sync.py + sync_state.py` | 3 | 6 | — |
| `dashboard_state` | #1122 | `core/opening_sync.py + sync_state.py` | 7 | 4 | — |
| `acquisition_ledger_records` | #1121 | `core/kobo_backup.py + acquisition_engine.py` | 1 | 1 | — |
| `reconcile_acquisitions` | #1121 | `core/kobo_backup.py + acquisition_engine.py` | 3 | 2 | — |
| `kobo_backup_records` | #1121 | `core/kobo_backup.py + acquisition_engine.py` | 1 | 0 | — |
| `kobo_backup_enabled` | #1121 | `core/kobo_backup.py + acquisition_engine.py` | 1 | 4 | — |
| `kobo_backup_interval_minutes` | #1121 | `core/kobo_backup.py + acquisition_engine.py` | 1 | 0 | — |
| `_kobo_backup_target_dir` | #1121 | `core/kobo_backup.py + acquisition_engine.py` | 5 | 2 | — |
| `_manifest_author` | #1121 | `core/kobo_backup.py + acquisition_engine.py` | 0 | 0 | — |
| `_kobo_backup_error_info` | #1121 | `core/kobo_backup.py + acquisition_engine.py` | 0 | 0 | — |
| `kobo_backup_diagnostic` | #1121 | `core/kobo_backup.py + acquisition_engine.py` | 1 | 2 | — |
| `_kobo_backup_due` | #1121 | `core/kobo_backup.py + acquisition_engine.py` | 2 | 0 | — |
| `sync_kobo_backup` | #1121 | `core/kobo_backup.py + acquisition_engine.py` | 1 | 8 | InterProcessLock, acquisition_lock_file |
| `kobo_unlinked_local_count` | #1121 | `core/kobo_backup.py + acquisition_engine.py` | 2 | 1 | — |
| `_sync_kobo_backup_locked` | #1121 | `core/kobo_backup.py + acquisition_engine.py` | 16 | 1 | — |
| `_account_for_loan` | #1120 | `core/loan_acquisition.py + acsm_cache.py` | 2 | 1 | — |
| `_acquisition_work_dir` | #1120 | `core/loan_acquisition.py + acsm_cache.py` | 1 | 1 | — |
| `_cleanup_acsm_after_success` | #1120 | `core/loan_acquisition.py + acsm_cache.py` | 0 | 1 | — |
| `_cleanup_old_acsm_work` | #1120 | `core/loan_acquisition.py + acsm_cache.py` | 2 | 0 | — |
| `acquire_digital_loan` | #1120 | `core/loan_acquisition.py + acsm_cache.py` | 7 | 7 | — |
| `prepare_digital_download` | #1120 | `core/loan_acquisition.py + acsm_cache.py` | 1 | 1 | — |
| `_download_filename` | #1120 | `core/loan_acquisition.py + acsm_cache.py` | 0 | 0 | — |

### Manual ACSM import orchestration (#1120)

`core/manual_acsm_import.py` contains the existing manual import implementation.
`LibraryService.import_acsm` retains its dedicated `manual-acsm.lock`, busy error,
identity selection and action recording; both facade signatures are unchanged.
Filesystem/hash/inspection/profile-error dependencies are injected at call time,
including historical `cifereca.services` monkeypatch points. The late binding
capture import remains at the same execution point, with its relative namespace
adjusted for the Core package. Executable AST otherwise remains identical.

Source preservation, content-derived staging, collision refusal, profile choice,
runtime payload, output validation, indexing, success-only staging cleanup and
failure persistence keep their original order. A failure before publication may
be retried explicitly; an output published before an indexing interruption is
preserved and blocks a second fulfillment. Tests use synthetic ACSM/runtime data
and local SQLite, not a real Adobe account or fulfillment.

### Adobe-first Paris return orchestration (#1120)

`core/return_adept_orchestrator.py` now also executes the existing guarded
Adobe-first/Paris-verification body. Both `LibraryService` signatures and the
shared cross-channel loan lock remain unchanged. The facade retains its late
UUID/public-helper imports at the same execution point and injects those bindings,
its exception class and logger; the remaining executable AST is identical.

Prior PNB requests still prevent an Adobe mutation. The durable Adobe claim is
written before the call; repeated clicks, uncertainty and restart recover from
inventories only. Paris inventory confirmation remains distinct from Adobe return,
and a PNB POST still requires its separate explicit action. No profile, token,
retry, provider payload or cleanup rule is changed. Existing synthetic concurrency,
crash/restart and provenance suites validate the boundary without a real return.

### Synchronization boundaries

`core/sync_coordination.py` orchestrates explicit account/opening synchronization
and event recording through the existing facade-owned dependencies.
`core/sync_projection.py` reads persisted action, inventory and dashboard state;
these reads do not trigger authentication, scans or acquisition. Inter-process
lock factories and paths are supplied by the facade to preserve coordination.

### Return verification boundary (#1120)

`core/loan_return_verification.py` groups PNB-return verification, the fresh Paris
inventory check following Adobe return, and Adobe inventory-only crash recovery.
All three `LibraryService` signatures remain unchanged. The logger and exception
seams are injected at call time; public guard imports and branch-local datetime
imports retain their execution points. Executable bodies are AST-identical after
adjusting relative import namespaces.

The existing 180-second in-flight POST guard, uncached exact borrower reads,
DILICOM completeness checks, checkpoint transitions, loan ID/provider matching,
origin-profile resolution and exact returned/token predicates retain their order.
These functions never send a return command, acquire content or delete a file;
PNB checkpoint verification still persists its original transition. Public return
wrappers retain the shared locks and all callers keep their historical seams.
Tests use synthetic inventories/local SQLite; they do not attest a real return.

### Provider loan return orchestration (#1120)

`core/provider_loan_return.py` contains the existing guarded PNB-only return
body. The `LibraryService` facade keeps both signatures, the cross-channel
per-loan lock, lock path and busy response. Its logger is injected at call time;
UUID and public return/checkpoint helpers are still imported at their original
execution points. Executable AST is unchanged after adapting relative imports.

Non-Paris delegation, exact borrower/source scope, cross-identity refusal, Adobe
checkpoint precedence, durable SQLite claim, at-most-once POST, indeterminate
network failure, cache invalidation and fresh verification/explicit retry retain
their original order. No Adobe return, file deletion or new retry is introduced.
Existing acceptance/concurrency/restart suites use synthetic provider responses
and local SQLite; real PNB or Adobe restitution is not attested by these tests.

### Kobo backup stages

`core/kobo_backup.py` keeps Vault manifest authority and negotiated artifact
execution, with separate stages for interrupted local remnants, metadata
refresh, acquisition decisions, execution/persistence, success-only pruning
and summary publication. Failed or partial runs preserve manifest rights and
local artifacts; locks and the existing facade entry points remain unchanged.

## ADEPT inventory reconciliation boundary (#1120)

`core/adept_inventory_reconciliation.py` delegates the existing acquisition to
public-inventory reconciliation. The private facade signature and the late
`adept_inventory.match_local_book` import remain in `LibraryService`; the matcher,
exception class and clock are injected at call time. The remaining executable
body is AST-identical to its original form.

The existing caller-owned cache reuses profile lists and inventories per exact
profile-name tuple. Recorded loan IDs retain precedence; incomplete inventories,
conflicts and ambiguity keep historical IDs/files and disable return. Persistence
and diagnostic order are unchanged. This boundary never acquires ACSM, fulfills,
creates a profile, returns a loan or mutates local files. Synthetic registry tests
with local SQLite do not validate a real Adobe/Paris reconciliation.

## Opening synchronization guard summary (#1120)

Each identity's opening result and persisted sync-run `detail` contain integer
`skipped_borrower_binding` and `skipped_cross_identity` counters. They count only
`attention_required` results with exact Core codes
`adept_borrower_binding_required` and `adept_loan_identity_conflict`, respectively.
They are subsets of `attention_required`, not additional errors or acquisitions.
The `identity_end` progress event carries the same counts.

The completion feedback names missing borrower associations and identity conflicts
using these counts and fixed labels, and remains a persistent warning when either
is nonzero. A technically completed `opening-sync` task is not evidence that its
loans were acquired. Existing guards, identity/source partial outcomes, acquisition
permissions and retry policy are unchanged. No title, profile, account, loan ID or
URL is added to these counters or feedback labels. The acceptance test reproduces
four missing borrower bindings followed by four cross-identity refusals with real
Core guards/local SQLite and synthetic inventory, with zero ACSM/fulfillment calls.

## Initial automatic-loan admission boundary (#1120)

`core/loan_acquisition_admission.py` resolves the existing persisted connection
owner, borrower refusal, cross-identity acquisition/active-right conflicts and
local entitlement selection before the automatic handler inspects artifacts.
A private frozen context returns either the original public refusal dictionary,
or the same identity object and entitlement ID previously held in local variables.
This context is not an acquisition grant; later artifact/retry/DRM checks remain
in the guarded handler under its original locks.

Conditions, source reads, logs and refusal values are AST-identical after
unwrapping the private result envelope. The rest of the handler is unchanged,
including the separate borrower revalidation immediately before ADEPT. Its
branch-local borrower code is reinitialized there; the historical `locals()`
seams concern only profile/destination and are untouched. No provider call,
artifact inspection, profile selection or ledger/catalog write occurs in this
phase. It never falls back to the current UI identity. Tests cover 0/1/N rights,
unbound/linked borrowers and cross-identity evidence from both ledger and rights,
with the real caller bridge preserving refusals and leaving state unchanged.

## Read-only ADEPT borrower boundary

`core/adept_borrower.py` resolves the proven Paris borrower/account context and
checks competing identity evidence in the existing acquisition and entitlement
ledgers. It reuses facade-owned accounts and bindings and performs no writes or
network operation. The service retains acquisition mutations, locks and fulfillment. Explicit
ADEPT acquisition return orchestration is delegated as described below. Historical status proof remains in `adept_return_origin.py`.


## Explicit ADEPT acquisition return boundary (#1120)

`core/adept_acquisition_return.py` owns the existing guarded return body for an
acquisition with a recorded ADEPT origin. `LibraryService.return_adept_acquisition`
keeps the global inter-process lock, its path and busy error; the private facade
method keeps its signature. Exception classes, DRM request constructor and clock
are injected at call time from `cifereca.services` so historical monkeypatches
remain effective.

The extracted executable body is AST-identical to its pre-extraction form,
including fresh exact-loan inventory before/after mutation, origin-profile
selection, pending/failure/confirmed writes, diagnostics and artifact preservation.
No acquisition, profile creation, retry policy, provider payload or return rule is
changed. The automatic-loan acquisition handler and the Paris return orchestrator
remain at their existing boundaries. Validation uses synthetic registry/runtime
responses and real local SQLite; it does not attest a real Adobe/Paris return.

## Local automatic-loan preparation (#1120)

`core/loan_local_preparation.py` orchestrates the bounded local phase before
provider acquisition: candidate inspection, invalidation in original order,
exact prior-ledger classification, optional catalog relinking, reuse and
persisted ADEPT retry refusal. `loan_artifact_state` remains read-only.
The facade retains both locks, the original exception boundary (this phase
precedes the acquisition try), source admission and all provider/ADEPT work.

A private immutable context carries the original previous state, decision and
optional public early outcome. Executable phase statements are AST-identical
after unwrapping that outcome; subsequent acquisition and failure handling
retain previous metadata/path, legacy mode and decision. Inspector, policy,
retry guard, clock and logger are injected from services at call time to
preserve historical monkeypatches. No rules, payloads or new permissions change.
Tests exercise synthetic local files and storage; real Adobe/Paris remains
unvalidated.
