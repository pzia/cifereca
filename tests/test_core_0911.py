import ast,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from cifereca.core import CiferecaCore
from cifereca.gui.facet_text import author_search_text
from cifereca.plugin_api import AttentionRequired
from cifereca.models import Loan, Account
from cifereca.kobo.errors import KoboAdeptError
from cifereca.acquisition import Acquisition

class Core0911(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.config=self.root/'config.json';self.db=self.root/'db'
        self.core=CiferecaCore.open(config_path=self.config,database_path=self.db)
        self.service=self.core.library
        self.core.config.set('providers.accounts',[])
        self.core.config.set('library.directory',str(self.root/'EBooks'))
    def tearDown(self):
        from cifereca.logging_utils import set_diagnostics_level
        set_diagnostics_level("normal")
        self.core.close();self.tmp.cleanup()
    def test_diagnostic_levels_persist_across_reopen(self):
        for level in ['trace','errors','verbose','normal']:
            self.service.save_diagnostic_preferences(enabled=True,level=level)
            fresh=CiferecaCore.open(config_path=self.config,database_path=self.root/('db-'+level))
            try:self.assertEqual(fresh.diagnostics.level,level)
            finally:fresh.close()
    def test_legacy_disabled_preference_cannot_disable_event_collection(self):
        self.service.save_diagnostic_preferences(enabled=False,level='trace')
        fresh=CiferecaCore.open(config_path=self.config,database_path=self.root/'db2')
        try:self.assertTrue(fresh.diagnostics.enabled);self.assertEqual(fresh.diagnostics.level,'trace')
        finally:fresh.close()
    def test_runtime_override_does_not_overwrite_saved_level(self):
        self.service.save_diagnostic_preferences(enabled=True,level='errors')
        fresh=CiferecaCore.open(config_path=self.config,database_path=self.root/'db2',diagnostics_level='trace')
        try:self.assertEqual(fresh.diagnostics.level,'trace');self.assertEqual(fresh.config.get('diagnostics.level'),'errors')
        finally:fresh.close()
    def test_save_failure_does_not_apply_preference(self):
        before=json.dumps(self.core.config.data,sort_keys=True)
        with patch('cifereca.config.Config.save',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.service.save_diagnostic_preferences(enabled=False,level='trace')
        self.assertEqual(json.dumps(self.core.config.data,sort_keys=True),before)
        self.assertTrue(self.core.diagnostics.enabled)
    def test_unknown_level_rejected(self):
        with self.assertRaises(ValueError):self.service.save_diagnostic_preferences(enabled=True,level='invalid')
        self.assertFalse(self.config.exists())
    def test_preferences_keep_identity_and_other_settings(self):
        original=self.core.config.identities()
        self.core.config.set('custom.setting','keep')
        self.service.save_diagnostic_preferences(enabled=True,level='detailed')
        self.assertEqual(self.core.config.identities(),original)
        self.assertEqual(self.core.config.get('custom.setting'),'keep')
        self.assertEqual(self.core.diagnostics.level,'verbose')
    def test_author_roles_and_commas_removed(self):
        self.assertEqual(author_search_text('Auteur : Harari, Yuval Noah'),'Harari Yuval Noah')
        self.assertEqual(author_search_text('Harari, Yuval Noah. Auteur'),'Harari Yuval Noah')
        self.assertEqual(author_search_text('Le Guin, Ursula K. (Auteur)'), 'Le Guin Ursula K')
    def test_author_accents_apostrophes_hyphen_preserved(self):
        self.assertEqual(author_search_text("Auteur : D’Ormesson, Jean-Pierre"),'D’Ormesson Jean-Pierre')
    def test_author_facet_preserves_field(self):
        from dataclasses import dataclass
        @dataclass
        class State:text:str='';field:str='all'
        source=Path(__file__).resolve().parents[1]/'src/cifereca/gui/facet_value_controller.py'
        from cifereca.gui.facet_value_controller import search_facet
        window=NS(search_controls=NS(set_state=Mock()),navigate=Mock(),run_search=Mock())
        search_facet(window,'author','Auteur : Harari, Yuval Noah',NS(state=lambda:State()))
        state=window.search_controls.set_state.call_args.args[0]
        self.assertEqual((state.text,state.field),('Harari Yuval Noah','author'))
        window.run_search.assert_called_once_with(False)
    def loan(self):return Loan(id='loan',provider='DILICOM',medium='digital',record_id='123',title='Livre',source_provider='paris',source_credential='test',identity_id='default')
    def test_loan_download_requires_capability_and_connection(self):
        loan=self.loan();session=NS(connected=True,capabilities=NS(digital_download=True))
        with patch.object(self.service,'_session_for_source',return_value=session):
            self.assertTrue(self.service.can_download_loan(loan))
            session.capabilities.digital_download=False;self.assertFalse(self.service.can_download_loan(loan))
            session.capabilities.digital_download=True;session.connected=False;self.assertFalse(self.service.can_download_loan(loan))
            loan.medium='physical';self.assertFalse(self.service.can_download_loan(loan))
    def _loan_with_proven_borrower(self):
        self.core.storage.provider_connection_put({
            'identity_id':'default', 'provider_id':'paris',
            'credential_id':'test', 'label':'Paris'
        })
        self.service.accounts.append(Account(
            unique_id='primary', provider_id='paris',
            credential_id='test', identity_id='default'
        ))
        loan=self.loan()
        loan.source_account_id='primary'
        return loan
    def test_provider_error_origin_preserved(self):
        self.service.acquire_digital_loan=Mock(side_effect=AttentionRequired('Refus',code='pnb_redirect_refused'))
        result=self.service._acquire_active_loan_automatically(self._loan_with_proven_borrower())
        self.assertEqual(result['code'],'pnb_redirect_refused')
        self.assertEqual(self.core.storage.acquisitions(provider_id='paris')[0]['executor'],'provider')
    def failed_runtime(self,artifact=False):
        loan=self._loan_with_proven_borrower();self.core.storage.adept_profile_binding_set('identity','default','hb')
        acsm=self.root/'loan.acsm';acsm.write_text('<fulfillmentToken><format>application/epub+zip</format></fulfillmentToken>')
        self.service.acquire_digital_loan=Mock(return_value=Acquisition(provider_id='paris',credential_id='test',account_id='primary',item_id='123',loan_id='loan',kind='acsm',source_url='https://example.invalid/acsm',local_path=acsm,mime_type='application/vnd.adobe.adept+xml',drm='adept',acsm_path=acsm,target_dir=self.root))
        self.service.kobo_profiles=Mock(return_value=[])
        self.service.runtime_diagnostic=Mock(return_value={'returncode':0,'duration_ms':166206,'stdout_bytes':0,'stderr_bytes':82,'stdin':'secret-key','stdout':'secret-key','operation':'secret-key'})
        def fail(request):
            if artifact:Path(request['destination']).write_bytes(b'output-not-yet-validated')
            raise KoboAdeptError('JSON invalide',code='kobo_adept_invalid_json',exit_code=1)
        self.service.execute_acquisition_request=fail
        self.core.diagnostics.level='errors'
        return self.service._acquire_active_loan_automatically(loan),acsm
    def test_json_failure_process_statistics_visible_at_errors_level(self):
        result,acsm=self.failed_runtime()
        self.assertEqual(result['process'],{'returncode':1,'duration_ms':166206,'stdout_bytes':0,'stderr_bytes':82,'artifact_present':False})
        self.assertTrue(acsm.exists())
        self.assertIn('returncode=1',result['error'])
        self.assertNotIn('secret-key',json.dumps(result))
        entries=self.core.diagnostics.snapshot();self.assertTrue(any(e['operation']=='acquisition failed' for e in entries))
        self.assertEqual(self.core.storage.acquisitions(provider_id='paris')[0]['executor'],'kobo-adept')
    def test_json_failure_after_artifact_never_claims_success(self):
        result,acsm=self.failed_runtime(artifact=True)
        # Runtime JSON corruption is recoverable; it must not claim the
        # artifact was validated or label a definitive Adobe license refusal.
        self.assertEqual(result['status'],'retryable_error');self.assertTrue(result['process']['artifact_present']);self.assertTrue(acsm.exists())
        self.assertGreater(result['process']['artifact_bytes'],0)

    def test_gui_diagnostic_change_saves_without_global_save(self):
        source=Path(__file__).resolve().parents[1]/'src/cifereca/gui/settings_diagnostic_actions.py'
        fn=next(n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.FunctionDef) and n.name=='persist_diagnostics')
        window=NS(service=self.service,show_error=Mock())
        refresh=Mock()
        ns={'self':window,'level_drop':NS(get_selected=lambda:3),'levels':['errors','normal','verbose','trace'],'diag_switch':NS(get_active=lambda:True),'refresh_text':refresh,'page_lifetime':NS(alive=True)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(source),'exec'),ns)
        ns['persist_diagnostics']()
        self.assertEqual(json.loads(self.config.read_text())['diagnostics']['level'],'trace')
        refresh.assert_called_once();window.show_error.assert_not_called()
