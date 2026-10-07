"""Run the real nested GTK settings handlers with simple editor doubles."""
import ast
from copy import deepcopy
from pathlib import Path
import re,shlex,tempfile,unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from cifereca.config import Config

class Value:
    def __init__(self,text='',active=False):self.text=text;self.active=active
    def get_text(self):return self.text
    def get_active(self):return self.active
    def get_selected(self):return 0

class SaveSettings(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.config=Config(Path(self.tmp.name)/'config.json')
        self.config.set('identities',[{'id':'stable-id','label':'Ma bibliothèque','directory_name':'ma-bibliotheque'}]);self.config.set('providers.accounts',[]);self.config.set('library.directory',str(Path(self.tmp.name)/'EBooks'))
        self.runner=NS(submit=Mock());self.service=NS(identities=[NS(identity_id='stable-id',directory_name='ma-bibliotheque')],save_configuration=Mock(return_value={'migrated':1,'collisions':0}))
        self.window=NS(config=self.config,service=self.service,runner=self.runner,_active_tasks={},show_error=Mock(),diagnostics=NS(enabled=True,level='normal'),refresh_library=Mock(),refresh_recent=Mock(),_render_dashboard=Mock(),toast=Mock(),_configure_kobo_backup_timer=Mock(),reconnect_sources=Mock(),identity_dropdown=object(),_populate_accounts=Mock())
        editor={'id':Value('stable-id'),'label':Value('Hugues'),'directory':Value('hugues'),'adept_profile':Value(),'languages':Value('fr'),'prefer_vo':Value(active=False)}
        path=Path(__file__).resolve().parents[1]/'src/cifereca/gui/main_window.py';tree=ast.parse(path.read_text())
        nested={n.name:n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in {'save_settings','save_and_reconnect'}}
        ns={'__name__':'cifereca.gui.main_window','__package__':'cifereca.gui','self':self.window,'re':re,'shlex':shlex,'identity_editors':[editor],'account_editors':[],'descriptors':{},'public_ids':set(),'provider_download_editors':{},'levels':['normal'],'vault_profiles':['Android'],'TaskPriority':NS(USER_ACTION=0),'refresh_text':Mock(),'save':object(),'reconnect':object()}
        for name in ['global_cmd_row','global_post_row','global_dir_row','global_ebook_row','global_audio_row','backup_dir_row']:ns[name]=Value()
        ns.update(diag_switch=Value(active=True),level_drop=Value(),exp_switch=Value(),backup_switch=Value(),unlinked_switch=Value(),backup_interval_row=Value('60'),vault_profile_drop=Value(),library_dir_row=Value(self.config.get('library.directory')))
        future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[future]+list(nested.values()),type_ignores=[])),str(path),'exec'),ns)
        self.ns=ns
    def tearDown(self):self.tmp.cleanup()
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
        self.assertTrue(self.ns['save_settings']())
        args=self.runner.submit.call_args.args
        args[3](RuntimeError('save failed'))
        self.window._populate_accounts.assert_not_called()
        self.window._render_dashboard.assert_not_called()
        self.assertEqual(self.service.identities[0].identity_id,'stable-id')
