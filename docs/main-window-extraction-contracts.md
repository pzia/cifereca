# Contrats de modularisation MainWindow

Cette baseline précède les extractions CoreUI. `main-window-inventory.json`
contient chaque méthode, ses attributs directs, callbacks, tâches et fonctions
imbriquées, ainsi que les tests candidats et leurs assertions exactes. Les
candidats sont des références statiques au module, pas une preuve de dispatch.
Le fixture `tests/fixtures/main_window_facade.json` fige les signatures et
les descripteurs des cinq classes ainsi que `clear_box`/`fr_date`.

## Frontières et points d'injection

| Domaine | État / contrat | Extraction / preuve à conserver |
| --- | --- | --- |
| Détail et couvertures | `DetailPanel`, `HistoryCoverResolver`, sélection, cache, annulation | #1124 ; `book_detail_metadata`, `cover_loader`, `book_widgets` existants ; projection partagée puis rendu GTK |
| Master/detail et API plugin | modèles, selection, paned, mode étroit, `PublicBookMasterDetailView` | #1125 ; délégation publique identique et réexports de classes |
| Paramètres | éditeurs, revisions, handlers `save_settings`/`save_and_reconnect` | #1126/#1127 ; `settings_context` et `llm_settings` existants, construire/stager le modèle avant écriture worker |
| Recherche | generations, caches, requête typée, identité capturée | #1128 ; `query_model`, `query_controls`, `search_intent`; signatures de façade, callbacks contrôlés par generation |
| Bibliothèque/Récents | placement/provenance, sélection différée, facettes, caches | #1129 ; `book_actions`, `book_widgets`, `recent_time` existants ; identité/generation en entrée |
| Shell/navigation/host | routes, back/forward, pages plugins, menus, sidebar | #1130 ; `navigation_history`, `shell_model`, `plugin_view` existants ; host garde la façade, aucune boucle d'import |
| Prêts/sync | source/account context, erreurs, timers, actions explicites | #1131 ; `sync_view`, `loan_view`, `return_feedback` existants ; contrôle réseau dans service/brokers |
| Démarrage/tâches/ressources | shell avant connexion, main thread, closed gate, unsubscribe | #1132 ; `task_runner`, `task_queue`, lifecycle existants ; worker ne touche pas GTK |

Les composants reçoivent leur dépendance/facade explicitement ; ils n'importent
pas `MainWindow` à l'exécution. Le module historique réexporte les classes et
helpers, dont `clear_box` et `fr_date` consommés par `builtin_ui/system_page.py`.
`gui.__init__` reste lazy ; import Core/CLI/namespace GUI sans PyGObject.
Le lot préparatoire ne change ni produit ni interface.

## Migration des assertions source

Aucune assertion listée dans l'inventaire n'est supprimée. Lors du déplacement,
le test source suit le module réellement propriétaire, et le contrat de façade
reste vérifié. Une assertion d'ordre/délégation AST exige aussi un test ciblé
du résultat, des appels, de l'identité et du callback ; un faux objet seul ne
prouve pas un rendu GTK. La baseline d'API résout les réexports locaux pour ne
pas exiger que le corps reste physiquement dans `main_window.py`.

| Famille | Contrats/risques | Preuve comportementale à maintenir |
| --- | --- | --- |
| `test_core_book_detail_projection`, `test_core0927_detail_layout`, `test_core_ui_369_library_context` | projection commune, disposition, provenance | `book_detail_probe` réel + projections/identité ; source helper mise à jour sans retrait d'assertions |
| `test_settings_migration_099_dev2` | extraction/exec handlers imbriqués, staging, save worker/reconnexion | mêmes doubles de stockage et callbacks ; futur contrôleur settings teste success/failure/cancel et vraie GTK |
| `test_core_ui_interactive_startup` | ordre shell→visible avant providers, plugin refresh | shell réactif pendant tâche lente ; pas de nouveau widget reconstruit à la connexion |
| `test_core_ui_602_search_detail_identity`, tests async identité | generation et contexte au retour worker | sélection conservée/invalidée selon identité ; rendu dans main thread |
| `test_gui_compat`, `test_core_ui_settings_tabs` | widgets libadwaita compatibles, catégories | vraie GTK runtime cible et absence de API `EntryRow.set_subtitle` |
| navigation/plugin master-detail/source AST restants | voir assertions exactes JSON et tests ci-dessous | adapter chemin source + conserver test façade et recette ciblée du domaine |

## Baseline GTK avant extraction

Les recettes versionnées `mainwindow-baseline-1366` et `mainwindow-baseline-1600`
utilisent la vraie MainWindow, sa contribution Diagnostics et un fixture local
Camille/Alex ; le contrôle actif est simulé. Elles vérifient navigation pendant
une tâche en attente, résultat visible au retour, focus clavier, changement et
restauration d'identité par XTEST, fermeture normale et refus de nouvelle tâche.
Elles couvrent le shell et son lifecycle ; les listes vides ne prouvent pas une
sélection métier ni un téléchargement live. Pour chaque extraction, compléter
par le probe du domaine chargé (`book_detail_probe`, `search_filter_probe`, etc.).
Les captures et logs restent des artefacts externes, jamais du code produit.

## Inventaire des méthodes

| Classe | Méthode | Lignes | État direct | Signaux/tâches/idle |
| --- | --- | ---: | ---: | ---: |
| HistoryCoverResolver | `__init__` | 17 | 7 | 0 |
| HistoryCoverResolver | `close` | 2 | 1 | 0 |
| HistoryCoverResolver | `stats` | 6 | 3 | 0 |
| HistoryCoverResolver | `_key` | 5 | 0 | 0 |
| HistoryCoverResolver | `request` | 60 | 8 | 4 |
| DetailPanel | `__init__` | 32 | 11 | 0 |
| DetailPanel | `_cancel_meta_request` | 7 | 1 | 0 |
| DetailPanel | `empty` | 11 | 5 | 0 |
| DetailPanel | `_cover_and_title` | 56 | 3 | 0 |
| DetailPanel | `_group` | 96 | 3 | 3 |
| DetailPanel | `_append_content` | 2 | 1 | 0 |
| DetailPanel | `_action_focus_key` | 10 | 0 | 0 |
| DetailPanel | `_focused_action_key` | 6 | 1 | 0 |
| DetailPanel | `_clear_actions` | 9 | 6 | 0 |
| DetailPanel | `_detail_action_priority` | 26 | 0 | 0 |
| DetailPanel | `_actions` | 57 | 6 | 0 |
| DetailPanel | `show_presentation` | 50 | 10 | 0 |
| DetailPanel | `show_summary` | 19 | 1 | 0 |
| DetailPanel | `update_cached_book_details` | 47 | 7 | 1 |
| DetailPanel | `add_group` | 2 | 1 | 0 |
| DetailPanel | `add_actions` | 2 | 1 | 0 |
| DetailPanel | `_direct_download_action` | 9 | 2 | 1 |
| DetailPanel | `show_loan` | 67 | 7 | 6 |
| DetailPanel | `_decorate` | 4 | 1 | 0 |
| DetailPanel | `show_history` | 32 | 8 | 0 |
| DetailPanel | `show_booking` | 15 | 6 | 0 |
| DetailPanel | `show_search` | 43 | 5 | 0 |
| DetailPanel | `_show_description` | 38 | 1 | 1 |
| DetailPanel | `show_item_detail` | 84 | 6 | 1 |
| MasterDetailPage | `__init__` | 143 | 31 | 10 |
| MasterDetailPage | `_factory_setup` | 2 | 1 | 0 |
| MasterDetailPage | `_factory_bind` | 5 | 0 | 0 |
| MasterDetailPage | `_factory_unbind` | 4 | 0 | 0 |
| MasterDetailPage | `connect_selected` | 2 | 1 | 0 |
| MasterDetailPage | `_selection_changed` | 9 | 5 | 0 |
| MasterDetailPage | `select_key` | 13 | 3 | 0 |
| MasterDetailPage | `_set_narrow_detail_context` | 16 | 4 | 0 |
| MasterDetailPage | `_show_narrow_detail` | 8 | 6 | 0 |
| MasterDetailPage | `_show_narrow_list` | 10 | 6 | 0 |
| MasterDetailPage | `clear_rows` | 13 | 5 | 0 |
| MasterDetailPage | `set_total_only_count` | 3 | 2 | 0 |
| MasterDetailPage | `_item_count_label` | 2 | 0 | 0 |
| MasterDetailPage | `_update_count` | 12 | 5 | 0 |
| MasterDetailPage | `show_rows` | 41 | 7 | 0 |
| MasterDetailPage | `append_rows` | 13 | 5 | 0 |
| PublicBookMasterDetailView | `__init__` | 7 | 2 | 0 |
| PublicBookMasterDetailView | `widget` | 2 | 1 | 0 |
| PublicBookMasterDetailView | `append_toolbar` | 2 | 1 | 0 |
| PublicBookMasterDetailView | `show_rows` | 14 | 2 | 0 |
| PublicBookMasterDetailView | `set_list_state` | 6 | 1 | 0 |
| PublicBookMasterDetailView | `connect_selected` | 4 | 2 | 0 |
| PublicBookMasterDetailView | `show_detail` | 22 | 1 | 1 |
| PublicBookMasterDetailView | `empty_detail` | 6 | 1 | 0 |
| MainWindow | `__init__` | 93 | 60 | 3 |
| MainWindow | `action_button` | 19 | 1 | 0 |
| MainWindow | `_install_css` | 38 | 0 | 0 |
| MainWindow | `_cancel_notification_hide` | 9 | 1 | 0 |
| MainWindow | `_hide_notification` | 4 | 2 | 0 |
| MainWindow | `_dismiss_notification` | 3 | 2 | 0 |
| MainWindow | `toast` | 9 | 5 | 1 |
| MainWindow | `show_error` | 8 | 2 | 0 |
| MainWindow | `show_attention` | 12 | 1 | 0 |
| MainWindow | `_cancel_activity_hide` | 7 | 1 | 0 |
| MainWindow | `_refresh_activity_history` | 13 | 3 | 0 |
| MainWindow | `_show_pending_activity_error` | 14 | 6 | 0 |
| MainWindow | `_hide_activity_feedback` | 16 | 10 | 0 |
| MainWindow | `_dismiss_activity_feedback` | 5 | 4 | 0 |
| MainWindow | `_schedule_activity_reduce` | 6 | 3 | 1 |
| MainWindow | `_activity_result` | 33 | 11 | 0 |
| MainWindow | `_task_started` | 21 | 10 | 0 |
| MainWindow | `_task_progress` | 7 | 2 | 0 |
| MainWindow | `_task_finished` | 44 | 13 | 0 |
| MainWindow | `_functional_error_message` | 6 | 0 | 0 |
| MainWindow | `_busy_changed` | 6 | 1 | 0 |
| MainWindow | `_on_close_request` | 41 | 11 | 0 |
| MainWindow | `_build_startup` | 21 | 5 | 2 |
| MainWindow | `connect_configured_sources` | 41 | 9 | 2 |
| MainWindow | `reconnect_sources` | 2 | 1 | 0 |
| MainWindow | `activate_startup_plugins` | 24 | 7 | 1 |
| MainWindow | `logout` | 4 | 1 | 0 |
| MainWindow | `_open_prelogin_diagnostics` | 30 | 2 | 3 |
| MainWindow | `_build_main_shell` | 67 | 22 | 2 |
| MainWindow | `_plugins_changed` | 15 | 10 | 0 |
| MainWindow | `_subscribe_cached_book_details` | 14 | 4 | 1 |
| MainWindow | `_cached_book_cache_invalidated` | 8 | 3 | 0 |
| MainWindow | `_cancel_cached_book_details` | 3 | 2 | 0 |
| MainWindow | `_request_cached_book_details` | 38 | 9 | 1 |
| MainWindow | `_add_nav_row` | 9 | 2 | 0 |
| MainWindow | `_build_standard_page` | 13 | 13 | 0 |
| MainWindow | `_rebuild_plugin_pages` | 62 | 14 | 1 |
| MainWindow | `_toggle_nav_group` | 5 | 3 | 0 |
| MainWindow | `_install_shell_shortcuts` | 21 | 10 | 1 |
| MainWindow | `_quit_application` | 6 | 2 | 0 |
| MainWindow | `_open_pages_menu` | 4 | 0 | 0 |
| MainWindow | `_focus_search` | 11 | 3 | 0 |
| MainWindow | `_show_settings` | 5 | 2 | 0 |
| MainWindow | `_cycle_page` | 5 | 2 | 0 |
| MainWindow | `_show_shortcuts` | 4 | 0 | 0 |
| MainWindow | `_create_shell_header` | 83 | 21 | 9 |
| MainWindow | `_ensure_pages_menu_rightmost` | 18 | 0 | 0 |
| MainWindow | `_refresh_header_actions` | 44 | 11 | 2 |
| MainWindow | `_invoke_ui_action` | 4 | 2 | 0 |
| MainWindow | `_show_route` | 31 | 19 | 0 |
| MainWindow | `_navigation_state` | 6 | 1 | 0 |
| MainWindow | `_update_navigation_buttons` | 6 | 2 | 0 |
| MainWindow | `_visit_route` | 7 | 5 | 0 |
| MainWindow | `_restore_navigation_state` | 22 | 10 | 0 |
| MainWindow | `can_go_back` | 2 | 1 | 0 |
| MainWindow | `can_go_forward` | 2 | 1 | 0 |
| MainWindow | `go_back` | 3 | 2 | 0 |
| MainWindow | `go_forward` | 3 | 2 | 0 |
| MainWindow | `_nav_selected` | 4 | 1 | 0 |
| MainWindow | `navigate` | 9 | 3 | 0 |
| MainWindow | `_replace_dropdown_labels` | 7 | 0 | 0 |
| MainWindow | `_populate_account_dropdown` | 17 | 5 | 0 |
| MainWindow | `_populate_accounts` | 13 | 6 | 0 |
| MainWindow | `_identity_changed` | 8 | 3 | 0 |
| MainWindow | `open_identity_library` | 3 | 2 | 0 |
| MainWindow | `open_identity_view` | 16 | 7 | 0 |
| MainWindow | `_activate_identity` | 75 | 43 | 1 |
| MainWindow | `_account_changed` | 33 | 12 | 0 |
| MainWindow | `_build_dashboard_page` | 22 | 4 | 2 |
| MainWindow | `_sync_status_label` | 2 | 0 | 0 |
| MainWindow | `_refresh_statistics` | 7 | 2 | 0 |
| MainWindow | `_show_statistics` | 26 | 1 | 0 |
| MainWindow | `_render_dashboard` | 60 | 7 | 2 |
| MainWindow | `_choose_identity_directory` | 20 | 2 | 1 |
| MainWindow | `_choose_adept_profile` | 20 | 3 | 2 |
| MainWindow | `_show_sync_issues` | 11 | 2 | 1 |
| MainWindow | `_build_sync_page` | 30 | 8 | 3 |
| MainWindow | `_refresh_sync_page` | 55 | 7 | 3 |
| MainWindow | `_build_tools_page` | 12 | 3 | 1 |
| MainWindow | `_refresh_tools_page` | 69 | 6 | 3 |
| MainWindow | `run_opening_sync` | 27 | 10 | 2 |
| MainWindow | `_known_item_languages` | 14 | 1 | 0 |
| MainWindow | `_build_loans_page` | 31 | 9 | 4 |
| MainWindow | `_loan_filter_toggled` | 4 | 2 | 0 |
| MainWindow | `_populate_loans` | 6 | 5 | 0 |
| MainWindow | `_render_loans` | 49 | 10 | 0 |
| MainWindow | `_loan_selected` | 10 | 6 | 0 |
| MainWindow | `refresh_loans` | 15 | 3 | 1 |
| MainWindow | `_loans_refreshed` | 10 | 4 | 0 |
| MainWindow | `confirm_renew` | 6 | 1 | 1 |
| MainWindow | `_renew` | 5 | 3 | 1 |
| MainWindow | `confirm_return` | 33 | 2 | 2 |
| MainWindow | `_return_loan_result` | 9 | 3 | 0 |
| MainWindow | `_return_loan` | 8 | 3 | 1 |
| MainWindow | `_return_loan_pnb` | 8 | 3 | 1 |
| MainWindow | `_query_controls` | 21 | 3 | 0 |
| MainWindow | `_reset_search_enter` | 4 | 0 | 0 |
| MainWindow | `_search_controls_changed` | 3 | 2 | 0 |
| MainWindow | `_search_enter` | 10 | 4 | 0 |
| MainWindow | `_search_key_interaction` | 4 | 1 | 0 |
| MainWindow | `_build_search_page` | 29 | 15 | 5 |
| MainWindow | `search_books` | 29 | 3 | 0 |
| MainWindow | `run_search` | 60 | 18 | 3 |
| MainWindow | `_sync_search_detail_after_results` | 7 | 2 | 0 |
| MainWindow | `_populate_local_search` | 7 | 6 | 0 |
| MainWindow | `_populate_search` | 11 | 6 | 0 |
| MainWindow | `_render_search_results` | 78 | 11 | 0 |
| MainWindow | `_update_search_count` | 7 | 2 | 0 |
| MainWindow | `_search_selected` | 79 | 24 | 4 |
| MainWindow | `_search_detail_request_context` | 8 | 3 | 0 |
| MainWindow | `_search_detail_context_current` | 10 | 3 | 0 |
| MainWindow | `_apply_search_detail` | 10 | 6 | 0 |
| MainWindow | `_search_detail_error` | 10 | 5 | 0 |
| MainWindow | `_build_history_page` | 11 | 5 | 2 |
| MainWindow | `_identity_request_current` | 7 | 2 | 0 |
| MainWindow | `refresh_history` | 17 | 6 | 1 |
| MainWindow | `_history_book_row` | 19 | 3 | 0 |
| MainWindow | `_rerender_history` | 3 | 1 | 0 |
| MainWindow | `_populate_history` | 26 | 11 | 0 |
| MainWindow | `_history_scroll_changed` | 8 | 4 | 1 |
| MainWindow | `_append_history_chunk` | 22 | 7 | 0 |
| MainWindow | `_history_selected` | 8 | 5 | 0 |
| MainWindow | `_build_bookings_page` | 6 | 2 | 1 |
| MainWindow | `refresh_bookings` | 17 | 6 | 1 |
| MainWindow | `_populate_bookings` | 16 | 4 | 0 |
| MainWindow | `_booking_selected` | 8 | 5 | 0 |
| MainWindow | `_build_library_page` | 48 | 12 | 6 |
| MainWindow | `_kobo_backup_status_label` | 18 | 0 | 0 |
| MainWindow | `_library_state_label` | 19 | 0 | 0 |
| MainWindow | `_library_availability_label` | 7 | 0 | 0 |
| MainWindow | `_library_filter_toggled` | 4 | 2 | 0 |
| MainWindow | `_library_search_online` | 4 | 4 | 0 |
| MainWindow | `choose_acsm` | 28 | 7 | 2 |
| MainWindow | `_search_facet` | 16 | 4 | 0 |
| MainWindow | `_facet_values` | 3 | 1 | 0 |
| MainWindow | `_facet_value_button` | 22 | 1 | 1 |
| MainWindow | `_facet_value_group` | 17 | 1 | 0 |
| MainWindow | `_attach_row_facets` | 22 | 2 | 0 |
| MainWindow | `_add_facets` | 126 | 6 | 2 |
| MainWindow | `_decorate_book_detail` | 6 | 5 | 0 |
| MainWindow | `_book_action_context` | 21 | 2 | 0 |
| MainWindow | `_add_plugin_book_actions` | 12 | 4 | 1 |
| MainWindow | `_known_search_result` | 12 | 0 | 0 |
| MainWindow | `_load_known_detail` | 9 | 7 | 1 |
| MainWindow | `refresh_library` | 29 | 9 | 1 |
| MainWindow | `_show_library_entries` | 4 | 3 | 0 |
| MainWindow | `open_book_detail` | 20 | 2 | 0 |
| MainWindow | `_open_library_item` | 12 | 5 | 0 |
| MainWindow | `_select_pending_library_item` | 38 | 5 | 0 |
| MainWindow | `_render_library` | 42 | 11 | 0 |
| MainWindow | `_build_recent_page` | 13 | 3 | 1 |
| MainWindow | `refresh_recent` | 28 | 9 | 1 |
| MainWindow | `_show_recent_entries` | 3 | 2 | 0 |
| MainWindow | `_render_recent` | 25 | 6 | 0 |
| MainWindow | `_add_local_book_actions` | 115 | 10 | 5 |
| MainWindow | `_open_placement` | 8 | 2 | 0 |
| MainWindow | `_add_catalogue_action` | 32 | 4 | 1 |
| MainWindow | `_book_description` | 3 | 0 | 0 |
| MainWindow | `_add_book_notice` | 18 | 1 | 0 |
| MainWindow | `_recent_selected` | 27 | 6 | 0 |
| MainWindow | `_render_kobo_loans` | 2 | 1 | 0 |
| MainWindow | `_book_recommendation_target` | 19 | 1 | 0 |
| MainWindow | `_add_book_recommendation_action` | 31 | 5 | 1 |
| MainWindow | `_library_selected` | 79 | 15 | 1 |
| MainWindow | `_kobo_selected` | 2 | 1 | 0 |
| MainWindow | `_configure_kobo_backup_timer` | 14 | 3 | 1 |
| MainWindow | `run_kobo_backup` | 60 | 6 | 2 |
| MainWindow | `choose_adept_loan` | 5 | 4 | 1 |
| MainWindow | `_present_adept_loan_choices` | 33 | 7 | 3 |
| MainWindow | `reconcile_adept_from_detail` | 8 | 5 | 1 |
| MainWindow | `return_adept_from_detail` | 19 | 7 | 1 |
| MainWindow | `prepare_pnb_for_loan` | 18 | 8 | 1 |
| MainWindow | `can_borrow_item` | 6 | 1 | 0 |
| MainWindow | `_eligible_borrow_accounts` | 9 | 1 | 0 |
| MainWindow | `borrow_search_item` | 39 | 8 | 2 |
| MainWindow | `download_direct_item` | 20 | 5 | 1 |
| MainWindow | `open_downloads_folder` | 5 | 2 | 0 |
| MainWindow | `open_library_folder` | 5 | 2 | 0 |
| MainWindow | `_build_settings_page` | 1006 | 23 | 32 |
| MainWindow | `refresh_diagnostics_text` | 4 | 0 | 0 |

## Tests candidats existants

- `tests/test_095_consolidation.py` — source/structure
- `tests/test_adept_manual_binding.py` — source/structure
- `tests/test_adept_ui_contract.py` — source/structure
- `tests/test_ci_core_test_selection.py` — source/structure
- `tests/test_ci_main_path_filters.py` — source/structure
- `tests/test_cifereca_step_a.py` — source/structure
- `tests/test_cifereca_step_g.py` — source/structure
- `tests/test_core044_performance.py` — source/structure
- `tests/test_core0923_gtk_focus.py` — source/structure
- `tests/test_core0925_open_destinations.py` — source/structure
- `tests/test_core0927_detail_layout.py` — source/structure
- `tests/test_core0927_gtk_lifecycle.py` — source/structure
- `tests/test_core0927_list_filter_contract.py` — source/structure
- `tests/test_core0927_loan_refresh.py` — source/structure
- `tests/test_core0929_cover_c03.py` — source/structure
- `tests/test_core0933_details_actions.py` — source/structure
- `tests/test_core0933_refresh_navigation.py` — source/structure
- `tests/test_core_0906_bridge_smoke.py` — source/structure
- `tests/test_core_0906_youth_search_contract.py` — source/structure
- `tests/test_core_0911.py` — source/structure
- `tests/test_core_0912_search_navigation.py` — source/structure
- `tests/test_core_0913.py` — source/structure
- `tests/test_core_0915.py` — source/structure
- `tests/test_core_0917.py` — source/structure
- `tests/test_core_0919.py` — source/structure
- `tests/test_core_0940_detail_actions.py` — source/structure
- `tests/test_core_0942_detail_metadata.py` — source/structure
- `tests/test_core_0984_return_feedback.py` — source/structure
- `tests/test_core_415_titlebar_action_registry.py` — source/structure
- `tests/test_core_458_unknown_medium.py` — source/structure
- `tests/test_core_464_document_links.py` — source/structure
- `tests/test_core_470_provider_search_capabilities.py` — source/structure
- `tests/test_core_591_book_recommendation_action.py` — source/structure
- `tests/test_core_account_scoped_secondary_async.py` — source/structure
- `tests/test_core_book_detail_projection.py` — source/structure
- `tests/test_core_public_book_navigation.py` — source/structure
- `tests/test_core_public_book_notice_actions.py` — source/structure
- `tests/test_core_public_link_boundary.py` — source/structure
- `tests/test_core_ui_095.py` — source/structure
- `tests/test_core_ui_270_responsive_search_library.py` — source/structure
- `tests/test_core_ui_313_progressive_search_feedback.py` — source/structure
- `tests/test_core_ui_346_local_dedupe.py` — source/structure
- `tests/test_core_ui_355_selection_filter.py` — source/structure
- `tests/test_core_ui_357_library_count.py` — source/structure
- `tests/test_core_ui_362_dense_detail.py` — source/structure
- `tests/test_core_ui_369_library_context.py` — source/structure
- `tests/test_core_ui_375_detail_action_order.py` — source/structure
- `tests/test_core_ui_377_compact_local_path.py` — source/structure
- `tests/test_core_ui_478_summary_order.py` — source/structure
- `tests/test_core_ui_544_list_viewport_width.py` — source/structure
- `tests/test_core_ui_559_non_overlapping_notifications.py` — source/structure
- `tests/test_core_ui_565_detail_facet_refresh.py` — source/structure
- `tests/test_core_ui_567_hide_satisfied_send_targets.py` — source/structure
- `tests/test_core_ui_585_activity_feedback.py` — source/structure
- `tests/test_core_ui_600_dense_book_rows.py` — source/structure
- `tests/test_core_ui_601_search_count.py` — source/structure
- `tests/test_core_ui_602_search_detail_identity.py` — source/structure
- `tests/test_core_ui_612_summary_flow.py` — source/structure
- `tests/test_core_ui_613_filterable_affordance.py` — source/structure
- `tests/test_core_ui_614_detail_action_overflow.py` — source/structure
- `tests/test_core_ui_616_library_header_actions.py` — source/structure
- `tests/test_core_ui_617_library_identity_detail.py` — source/structure
- `tests/test_core_ui_618_recent_timeline.py` — source/structure
- `tests/test_core_ui_632_identity_scoped_secondary_pages.py` — source/structure
- `tests/test_core_ui_633_loan_counts.py` — source/structure
- `tests/test_core_ui_634_loan_due_urgency.py` — source/structure
- `tests/test_core_ui_635_history_business_count.py` — source/structure
- `tests/test_core_ui_636_booking_states.py` — source/structure
- `tests/test_core_ui_641_sync_states.py` — source/structure
- `tests/test_core_ui_642_tools_preview.py` — source/structure
- `tests/test_core_ui_643_accounts_without_plugin_inventory.py` — source/structure
- `tests/test_core_ui_644_contextual_settings_actions.py` — source/structure
- `tests/test_core_ui_645_actionable_diagnostics.py` — source/structure
- `tests/test_core_ui_cached_detail_gtk.py` — source/structure
- `tests/test_core_ui_dashboard_actions.py` — source/structure
- `tests/test_core_ui_detail_badge_layout.py` — source/structure
- `tests/test_core_ui_diagnostic_actions.py` — source/structure
- `tests/test_core_ui_header_dropdown.py` — source/structure
- `tests/test_core_ui_identity_async_recent.py` — source/structure
- `tests/test_core_ui_interactive_startup.py` — source/structure
- `tests/test_core_ui_llm_settings.py` — source/structure
- `tests/test_core_ui_min_width.py` — source/structure
- `tests/test_core_ui_online_search_action.py` — source/structure
- `tests/test_core_ui_scoped_pending_navigation.py` — source/structure
- `tests/test_core_ui_search_holdings_generation.py` — source/structure
- `tests/test_core_ui_settings_tabs.py` — source/structure
- `tests/test_core_ui_startup_actions.py` — source/structure
- `tests/test_core_ui_sync_actions.py` — source/structure
- `tests/test_gui_compat.py` — source/structure
- `tests/test_identity_save_ui_contract.py` — source/structure
- `tests/test_regressions_099.py` — source/structure
- `tests/test_settings_migration_099_dev2.py` — source/structure
- `tests/test_shell_controls_097_dev5.py` — source/structure
- `tests/test_ui_iteration_097.py` — source/structure

## Detail ownership after the first extraction

`gui/history_cover_resolver.py` owns the lazy resolver; `gui/detail_panel.py`
owns the unchanged shared GTK renderer. `gui/detail_model.py` owns the same
neutral presentation and pure formatting helpers without GTK. Existing
`gui/action_widgets.py` now owns the common button and container helpers,
removing their duplicate definition. `main_window.py` keeps historical exports.
`tests/gui_source.py` follows these exact exports to read the real declarations;
all existing assertions continue to inspect their original bodies. It creates
no substitute implementation. Components receive the window host explicitly;
MainWindow is imported by DetailPanel only under TYPE_CHECKING.

## Master/detail component ownership

`master_detail.py` owns the virtualized list, stable-key reconciliation and
narrow pane lifecycle. `public_book_view.py` owns the public plugin adapter and
reuses the same component and URI validation boundary. Both are reexported from
MainWindow; runtime imports remain acyclic and the window is supplied explicitly.
Source contracts read the real declarations through the maintained owner map.

## Settings commit ownership

`settings_model.SettingsCommitController` owns immutable staging and a single
in-flight save through its success/error delivery. The scheduler and existing
service are injected; directory migration/rollback remain service-owned. Typed
error presentation stays in TaskRunner. The GTK editor retains validation and
revision tracking until their separate extraction; success refresh and delayed
reconnection keep their existing callbacks.

## Library and Recent refresh ownership

`library_refresh.py` owns the two snapshot task adapters, independently of GTK.
`MainWindow.refresh_library` and `refresh_recent` retain their signatures and
delegate with the caller's control and scan options. MainWindow still owns the
active identity, pending flags and separate request generations; the existing
TaskRunner delivers completions on the GTK thread. An obsolete identity or
request cannot publish results, clear a newer pending request or show its error.
Rendering, facets and pending book selection stay in their existing owners.
Cover reinspection remains explicit and adds no acquisition or provider call.

## Settings Diagnostics action ownership

`settings_diagnostic_actions.build_settings_diagnostic_callbacks` owns copy,
export, direct diagnostic preferences and clear callbacks. The window supplies
the page lifetime, report refresh, widgets and Gtk/Gdk adapters, and retains
all signal connections. Import and factory construction perform no collection,
clipboard access, file write or preference mutation. Clipboard verification and
native chooser responses keep the originating lifetime and report snapshot;
disposal prevents stale feedback or writes. Settings save/migration, diagnostics
projection and the independent AI consent flow remain in their existing owners.
