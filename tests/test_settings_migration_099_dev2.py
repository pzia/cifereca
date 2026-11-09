"""Run the actual settings commit binder with simple editor doubles."""
import ast
from copy import deepcopy
from pathlib import Path
import re,shlex,tempfile,unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from cifereca.config import Config
from cifereca.gui.settings_model import SettingsCommitController, SettingsRevisionState
from cifereca.gui.settings_draft import build_settings_draft, SettingsValidationError
from cifereca.gui.settings_commit_actions import bind_settings_commit

class Value:
    def __init__(self,text='',active=False):self.text=text;self.active=active
    def get_text(self):return self.text
    def get_active(self):return self.active
    def get_selected(self):return 0

class TestSavedAccountProbeButtons(unittest.TestCase):
    def test_newly_created_enabled_account_test_is_unlocked_only_after_save(self):
        from cifereca.gui.settings_commit_actions import (
            _refresh_saved_account_test_buttons,
        )
        button = Mock()
        editor = {
            "id": "la-rochelle-new", "provider": "biblioaccess.la-rochelle",
            "deleted": False, "invalid_saved_snapshot": False,
            "explicit_probe_button": button,
        }
        persisted = {
            "id": "la-rochelle-new", "provider": "biblioaccess.la-rochelle",
            "identity_id": "alice", "enabled": True,
        }
        _refresh_saved_account_test_buttons(
            NS(provider_accounts=lambda: [persisted]), [editor])
        button.set_sensitive.assert_called_once_with(True)

    def test_duplicate_disabled_and_unbound_accounts_keep_test_locked(self):
        from cifereca.gui.settings_commit_actions import (
            _refresh_saved_account_test_buttons,
        )
        button = Mock()
        editor = {
            "id": "la-rochelle-new", "provider": "biblioaccess.la-rochelle",
            "deleted": False, "invalid_saved_snapshot": False,
            "explicit_probe_button": button,
        }
        row = {
            "id": "la-rochelle-new", "provider": "biblioaccess.la-rochelle",
            "identity_id": "alice", "enabled": True,
        }
        for rows in ([], [row, dict(row)],
                     [{**row, "enabled": False}],
                     [{**row, "identity_id": ""}]):
            button.reset_mock()
            _refresh_saved_account_test_buttons(
                NS(provider_accounts=lambda rows=rows: rows), [editor])
            button.set_sensitive.assert_called_once_with(False)


class SaveSettings(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.config=Config(Path(self.tmp.name)/'config.json')
        self.config.set('identities',[{'id':'stable-id','label':'Ma bibliothèque','directory_name':'ma-bibliotheque'}]);self.config.set('providers.accounts',[]);self.config.set('library.directory',str(Path(self.tmp.name)/'EBooks'))
        self.runner=NS(submit=Mock(),present_error=Mock());self.service=NS(identities=[NS(identity_id='stable-id',directory_name='ma-bibliotheque')],save_configuration=Mock(return_value={'migrated':1,'collisions':0}))
        self.window=NS(config=self.config,service=self.service,runner=self.runner,_active_tasks={},show_error=Mock(),diagnostics=NS(enabled=True,level='normal'),refresh_library=Mock(),refresh_recent=Mock(),_render_dashboard=Mock(),toast=Mock(),_configure_kobo_backup_timer=Mock(),reconnect_sources=Mock(),identity_dropdown=object(),_populate_accounts=Mock())
        editor={'id':Value('stable-id'),'label':Value('Hugues'),'directory':Value('hugues'),'adept_profile':Value(),'languages':Value('fr'),'prefer_vo':Value(active=False)}
        ns={'__name__':'cifereca.gui.main_window','__package__':'cifereca.gui','self':self.window,'re':re,'shlex':shlex,'page_lifetime':NS(alive=True),'identity_editors':[editor],'account_editors':[],'descriptors':{},'public_ids':set(),'provider_download_editors':{},'levels':['normal'],'vault_profiles':['Android'],'TaskPriority':NS(USER_ACTION=0),'refresh_text':Mock(),'save':object(),'reconnect':object(),
            'settings_state':SettingsRevisionState(['accounts','library','sync','interface','ai','diagnostic']),
            'build_settings_draft':build_settings_draft,'SettingsValidationError':SettingsValidationError,
            'track_all_settings_widgets':Mock(),'update_settings_actions':Mock(),
            'refresh_sync_navigation':Mock()}
        for name in ['global_cmd_row','global_post_row','global_dir_row','global_ebook_row','global_audio_row','backup_dir_row']:ns[name]=Value()
        ns.update(diag_switch=Value(active=True),level_drop=Value(),exp_switch=Value(),backup_switch=Value(),backup_interval_row=Value('60'),vault_profile_drop=Value(),library_dir_row=Value(self.config.get('library.directory')))
        download_settings = NS(**{
            k: ns[k] for k in (
                'global_dir_row', 'global_ebook_row', 'global_audio_row',
                'global_cmd_row', 'global_post_row',
                'provider_download_editors',
            )
        })
        self.download_settings = download_settings
        ns['save_settings'], ns['save_and_reconnect'] = bind_settings_commit(
            self.window, page_lifetime=ns['page_lifetime'], settings_state=ns['settings_state'],
            identity_editors=ns['identity_editors'], account_editors=ns['account_editors'],
            descriptors=ns['descriptors'], public_ids=ns['public_ids'],
            download_settings=download_settings,
            application_ui=NS(diagnostics_switch=ns['diag_switch'], experimental_switch=ns['exp_switch'], level_dropdown=ns['level_drop'], levels=ns['levels']),
            sync_settings=NS(**{k: ns[k] for k in ('backup_switch', 'library_dir_row', 'backup_dir_row', 'backup_interval_row', 'vault_profiles', 'vault_profile_drop')}),
            settings_bars=NS(save=ns['save'], reconnect=ns['reconnect']),
            ai_ui=NS(status=Mock()), ai_cfg={}, capture_ai_settings=lambda: {},
            refresh_text=ns['refresh_text'], track_all_settings_widgets=ns['track_all_settings_widgets'],
            update_settings_actions=ns['update_settings_actions'], refresh_sync_navigation=ns['refresh_sync_navigation'],
        )
        ns['settings_state'].mark_dirty('accounts')
        self.ns=ns
    def tearDown(self):self.tmp.cleanup()
    def test_malformed_legacy_downloads_never_submit_unrelated_settings_save(self):
        # A corrupt provider download config can be rendered safely, but
        # saving a different Settings section must not replace its originals.
        before = deepcopy(self.config.data)
        self.download_settings.invalid_saved_snapshot = True
        self.assertFalse(self.ns['save_settings']())
        self.runner.submit.assert_not_called()
        self.service.save_configuration.assert_not_called()
        self.window.show_error.assert_called_once()
        self.assertIn(
            'téléchargement historiques invalides',
            self.window.show_error.call_args.args[0],
        )
        self.assertEqual(self.config.data, before)

    def test_changes_are_staged_and_migration_runs_in_worker(self):
        before=deepcopy(self.config.data);self.assertTrue(self.ns['save_settings']())
        self.assertEqual(self.config.data,before);self.service.save_configuration.assert_not_called()
        args=self.runner.submit.call_args.args;result=args[1]()
        self.assertEqual(self.service.save_configuration.call_args.args[0]['identities'][0]['directory_name'],'hugues')
        args[2](result);self.window.refresh_library.assert_called_once_with(False)
    def test_reconnection_waits_for_configuration_job(self):
        self.ns['save_and_reconnect']();self.window.reconnect_sources.assert_not_called()
        args=self.runner.submit.call_args.args;args[2](args[1]());self.window.reconnect_sources.assert_called_once()
    def test_directory_change_is_rejected_while_another_task_runs(self):
        before=deepcopy(self.config.data);self.window._active_tasks={1:'download'}
        self.assertFalse(self.ns['save_settings']());self.runner.submit.assert_not_called();self.assertEqual(self.config.data,before)

    def test_success_refreshes_identity_selector_delegations_and_dashboard(self):
        before_identity=self.service.identities[0].identity_id
        self.assertTrue(self.ns['save_settings']())
        args=self.runner.submit.call_args.args
        result=args[1]()
        args[2](result)
        self.window._populate_accounts.assert_called_once()
        self.assertGreaterEqual(self.window._render_dashboard.call_count,1)
        self.assertEqual(self.service.identities[0].identity_id,before_identity)
    def test_failed_save_does_not_refresh_identity_selection(self):
        self.service.save_configuration.side_effect=RuntimeError('save failed')
        self.assertTrue(self.ns['save_settings']())
        args=self.runner.submit.call_args.args
        with self.assertRaises(RuntimeError):
            args[1]()
        self.window._populate_accounts.assert_not_called()
        self.window._render_dashboard.assert_not_called()
        self.assertEqual(self.service.identities[0].identity_id,'stable-id')

    def test_edits_during_worker_delivery_remain_dirty_after_success(self):
        self.assertTrue(self.ns['save_settings']())
        args=self.runner.submit.call_args.args
        result=args[1]()
        self.ns['identity_editors'][0]['label'].text='Nouvelle modification'
        self.ns['settings_state'].mark_dirty('accounts')
        args[2](result)
        self.assertTrue(self.ns['settings_state'].dirty('accounts'))
        self.assertEqual(self.service.save_configuration.call_args.args[0]['identities'][0]['label'],'Hugues')

    def test_failed_delivery_keeps_revisions_pending_without_reconnection(self):
        self.ns['save_and_reconnect']()
        args=self.runner.submit.call_args.args
        args[3](RuntimeError('rollback'))
        self.assertTrue(self.ns['settings_state'].dirty('accounts'))
        self.window.reconnect_sources.assert_not_called()
        self.window._populate_accounts.assert_not_called()

    def test_settings_save_preserves_unknown_disabled_provider_without_activation(self):
        """#3731: unrelated Settings edits cannot erase or activate unknown plugins."""
        account = {
            'id': 'unrecognized-a', 'provider': 'future-provider',
            'identity_id': 'stable-id', 'label': 'Future source',
            'enabled': False, 'anonymous': False,
            'credentials': {'opaque': 'preserve-me'},
            'options': {'new_option': 'untouched'},
        }
        self.config.set('providers.accounts', [account])
        self.config.set('providers.future-provider', {'disabled': True, 'extension': {'x': 1}})
        self.assertTrue(self.ns['save_settings']())
        payload = self.runner.submit.call_args.args[1]()
        saved = self.service.save_configuration.call_args.args[0]
        self.assertEqual(saved['providers']['accounts'], [account])
        self.assertEqual(saved['providers']['future-provider'], {
            'disabled': True, 'extension': {'x': 1},
        })
        self.assertFalse(saved['providers']['accounts'][0]['enabled'])
        self.assertEqual(self.config.provider_accounts(), [account])

    def test_invalid_edit_does_not_queue_or_modify_configuration(self):
        before=deepcopy(self.config.data)
        self.ns['identity_editors'][0]['id'].text='invalid / id'
        self.assertFalse(self.ns['save_settings']())
        self.runner.submit.assert_not_called()
        self.assertEqual(self.config.data,before)
        self.window.show_error.assert_called_once()

    def test_legacy_experimental_flag_is_preserved_but_not_rewritten_by_ui(self):
        """#3731: unrelated Settings save never enables or resets a retired bit."""
        for stored in (True, False):
            self.config.set('experimental_actions', stored)
            # An obsolete UI widget must no longer be consulted at commit.
            self.ns['exp_switch'].get_active = Mock(
                side_effect=AssertionError('retired experimental switch consulted')
            )
            self.assertTrue(self.ns['save_settings']())
            task = self.runner.submit.call_args.args
            result = task[1]()
            saved = self.service.save_configuration.call_args.args[0]
            self.assertEqual(saved['experimental_actions'], stored)
            task[2](result)  # complete the pending async job before next save

    def test_legacy_kobo_flag_does_not_reconfigure_timer_after_save_or_reconnect(self):
        """#3198: a retired global bit is passive, including for old configs.

        Both Settings actions persist the value for rollback but never start
        or reconfigure a Kobo-specific timer. Per-source refresh is owned by
        the separate Sync broker and remains untouched.
        """
        for legacy_enabled in (True, False):
            self.config.set('kobo_backup.enabled', legacy_enabled)
            for reconnect_after in (False, True):
                if reconnect_after:
                    self.ns['save_and_reconnect']()
                else:
                    self.assertTrue(self.ns['save_settings']())
                task = self.runner.submit.call_args.args
                result = task[1]()
                saved = self.service.save_configuration.call_args.args[0]
                self.assertIs(saved['kobo_backup']['enabled'], legacy_enabled)
                task[2](result)
                self.window._configure_kobo_backup_timer.assert_not_called()
        self.assertEqual(self.window.reconnect_sources.call_count, 2)

    def test_settings_save_succeeds_on_coreui_without_retired_timer_port(self):
        """A neutral MainWindow can omit the legacy GUI backup timer API."""
        delattr(self.window, '_configure_kobo_backup_timer')
        self.assertTrue(self.ns['save_settings']())
        task = self.runner.submit.call_args.args
        task[2](task[1]())
        self.window._populate_accounts.assert_called_once()
        self.window.toast.assert_called_once_with(
            "Répertoire déplacé · 0 collision(s) préservée(s) · bibliothèque actualisée", 5
        )

    def test_new_installation_has_no_experimental_permission_default(self):
        from cifereca.config_defaults import DEFAULT_CONFIG
        self.assertNotIn('experimental_actions', DEFAULT_CONFIG)
