import ast
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from cifereca import acsm_cache
from tests.synthetic_lending_sessions import enable_synthetic_paris_opening
from cifereca.core import CiferecaCore
from cifereca.models import Account, Loan
from cifereca.identities import AdeptProfileResolver, AdeptProfileResolutionError

XML = '<fulfillmentToken xmlns="http://ns.adobe.com/adept"><format>application/epub+zip</format><title>Été</title></fulfillmentToken>'

class Core0913(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.core = CiferecaCore.open(config_path=self.root/'config.json', database_path=self.root/'db')
        self.service = self.core.library
        self.core.config.set('library.directory', str(self.root/'EBooks'))
        self.loan = Loan(id='loan', provider='PNB', medium='digital', record_id='123', title='Été', source_provider='paris', source_credential='test', source_account_id='reader', identity_id='default', due_at='2099-01-01T00:00:00Z', ebook_url='https://example.invalid/acsm?token=secret-token')
        self.directory = self.root/'work'; self.directory.mkdir()
        self.path = self.directory/'Été.acsm'; self.path.write_text(XML, encoding='utf-8')
    def tearDown(self):
        self.core.close(); self.tmp.cleanup()
    def remember(self, **kwargs):
        return acsm_cache.remember(self.path, self.directory, self.loan, 'default', 'reader', **kwargs)
    def reuse(self, loan=None, identity='default', account='reader', **kwargs):
        return acsm_cache.reuse(self.directory, loan or self.loan, identity, account, **kwargs)
    def test_valid_cache_unicode_and_no_token_in_manifest(self):
        self.assertTrue(self.remember()); self.assertEqual(self.reuse(), self.path)
        self.assertNotIn('secret-token', (self.directory/acsm_cache.MANIFEST).read_text())
        self.assertNotIn('https://', (self.directory/acsm_cache.MANIFEST).read_text())
    def test_legacy_input_requires_fresh_download(self):
        self.assertIsNone(self.reuse())
    def test_scope_changes_never_reuse(self):
        self.remember()
        for field, value in [('source_provider','other'), ('source_credential','other'), ('id','other'), ('record_id','other'), ('due_at','2098-01-01'), ('ebook_url','https://example.invalid/new')]:
            with self.subTest(field=field): self.assertIsNone(self.reuse(replace(self.loan, **{field:value})))
        self.assertIsNone(self.reuse(identity='other')); self.assertIsNone(self.reuse(account='other'))
    def test_ttl_and_future_timestamp(self):
        self.remember(now=100)
        self.assertEqual(self.reuse(now=100+acsm_cache.TTL-1), self.path)
        self.assertIsNone(self.reuse(now=100+acsm_cache.TTL)); self.assertIsNone(self.reuse(now=99))
    def test_expired_returned_and_invalid_due_not_reused(self):
        self.remember()
        for loan in [replace(self.loan,due_at='2000-01-01'),replace(self.loan,due_at='bad'),replace(self.loan,is_late=True),replace(self.loan,state='returned')]:
            self.assertIsNone(self.reuse(loan))
    def test_corrupted_content_and_manifest_not_reused(self):
        self.remember(); self.path.write_text(XML+'changed'); self.assertIsNone(self.reuse())
        (self.directory/acsm_cache.MANIFEST).write_text('{'); self.assertIsNone(self.reuse())
    def test_invalid_xml_or_dtd_not_remembered(self):
        for text in ['garbage','<html/>','<!DOCTYPE x [<!ENTITY a "x">]><fulfillmentToken/>','<fulfillmentToken>']:
            self.path.write_text(text); self.assertFalse(self.remember())
    def test_oversized_input_not_remembered(self):
        self.path.write_bytes(b'x'*(acsm_cache.MAX_BYTES+1)); self.assertFalse(self.remember())
    def test_path_traversal_and_symlinks_not_reused(self):
        self.remember(); mf=self.directory/acsm_cache.MANIFEST
        row=json.loads(mf.read_text()); row['file']='../Été.acsm'; mf.write_text(json.dumps(row)); self.assertIsNone(self.reuse())
        self.remember(); outside=self.root/'outside.acsm'; outside.write_text(XML)
        self.path.unlink(); self.path.symlink_to(outside); self.assertIsNone(self.reuse()); self.assertFalse(self.remember())
    def test_symlink_manifest_not_reused(self):
        self.remember(); mf=self.directory/acsm_cache.MANIFEST; outside=self.root/'sidecar'; mf.replace(outside); mf.symlink_to(outside)
        self.assertIsNone(self.reuse())
    def test_cleanup_only_matching_manifest(self):
        self.remember(); acsm_cache.forget(self.directory/'other.acsm'); self.assertTrue((self.directory/acsm_cache.MANIFEST).exists())
        self.service._cleanup_acsm_after_success(self.path,loan_id='loan'); self.assertFalse(self.directory.exists())
    def connect_provider(self):
        self.service.storage.provider_connection_put({'identity_id':'default','provider_id':'paris','credential_id':'test','label':'Paris'})
        self.service.accounts=[Account(display_name='Reader',unique_id='reader',provider_id='paris',credential_id='test',identity_id='default')]
        def download(loan, account, target):
            path=target/'Été.acsm'; path.write_text(XML,encoding='utf-8'); return path,loan.ebook_url
        self.session=NS(provider_id='paris', download_digital=Mock(side_effect=download))
        self.service._session_for_source=Mock(return_value=self.session)
        self.service.acquisition.run_post_download=Mock()
    def test_service_reuses_only_after_hook_success_and_force_refresh(self):
        self.connect_provider()
        first=self.service.acquire_digital_loan(self.loan); second=self.service.acquire_digital_loan(self.loan)
        self.assertEqual(first.path,second.path); self.session.download_digital.assert_called_once(); self.service.acquisition.run_post_download.assert_called_once()
        self.service.prepare_digital_download(self.loan,force_refresh=True); self.assertEqual(self.session.download_digital.call_count,2)
    def test_failed_hook_not_cached(self):
        self.connect_provider(); self.service.acquisition.run_post_download.side_effect=RuntimeError('hook')
        with self.assertRaises(RuntimeError): self.service.acquire_digital_loan(self.loan)
        self.service.acquisition.run_post_download.side_effect=None; self.service.acquire_digital_loan(self.loan)
        self.assertEqual(self.session.download_digital.call_count,2)
    def test_two_sync_attempts_then_profile_resolution_reuse_and_cleanup(self):
        self.connect_provider()
        self.service.kobo_profiles=Mock(return_value=[{'profile_id':'stable-hb','name':'hb','fetch_capable':True},{'profile_id':'stable-h','name':'heloise','fetch_capable':True}])
        self.service.execute_acquisition_request=Mock()
        self.service._persist_active_loans('default',[self.loan])
        for _ in range(2):
            result=self.service._acquire_active_loan_automatically(self.loan)
            self.assertEqual(result['status'],'attention_required'); self.assertEqual(result['code'],'adept_profile_ambiguous')
        self.session.download_digital.assert_called_once(); self.service.execute_acquisition_request.assert_not_called()
        self.service.storage.adept_profile_binding_set('identity','default','stable-hb')
        def execute(request):
            self.assertEqual(request['profile'],'hb')
            dest=Path(request['destination']); dest.parent.mkdir(parents=True,exist_ok=True)
            with zipfile.ZipFile(dest,'w') as z:
                z.writestr('mimetype','application/epub+zip'); z.writestr('META-INF/container.xml','<container/>'); z.writestr('content.xhtml','<html/>')
            return {'ok':True,'artifact':{'path':str(dest),'format':'epub'}}
        self.service.execute_acquisition_request.side_effect=execute
        # Correcting the binding is an explicit user intervention; the next
        # unattended opening must not silently replay an already failed ACSM.
        result=self.service._acquire_active_loan_automatically(self.loan)
        self.assertEqual(result['status'],'attention_required')
        self.assertEqual(result['retry'],'explicit_only')
        result=self.service._acquire_active_loan_automatically(self.loan,force_refresh=True)
        self.assertEqual(result['status'],'complete')
        self.assertEqual(self.session.download_digital.call_count,2)
        target=self.service._acquisition_work_dir('default','paris','loan')
        self.assertFalse((target/'Été.acsm').exists()); self.assertFalse((target/acsm_cache.MANIFEST).exists())
    def test_choices_use_stable_id_filter_readiness_and_deduplicate(self):
        self.service.kobo_profiles=Mock(return_value=[{'profile_id':'a','name':'hb','fetch_capable':True}, {'profile_id':'a','name':'alias','fetch_capable':True}, {'profile_id':'b','name':'unready','fetch_capable':True,'provisioning':{'acsm_fetch_ready':False}}, {'profile_id':'c','name':'ready','provisioning':{'acsm_fetch_ready':True}}, {'name':'unknown'}])
        self.assertEqual(self.service.adept_profile_choices(),[{'reference':'a','name':'hb'},{'reference':'c','name':'ready'}])
    def test_resolver_does_not_auto_choose_unready_profile(self):
        with self.assertRaises(AdeptProfileResolutionError):
            AdeptProfileResolver().resolve(profiles=[{'name':'a','fetch_capable':True,'provisioning':{'acsm_fetch_ready':False}}])
    def test_profile_lookup_errors_propagate(self):
        self.service.kobo_profiles=Mock(side_effect=RuntimeError('runtime unavailable'))
        with self.assertRaises(RuntimeError): self.service.adept_profile_choices()
    def test_chooser_requires_click_and_stages_stable_reference(self):
        from cifereca.gui.settings_chooser_controller import choose_adept_profile
        dialog=Mock(); window=NS(service=NS(adept_profile_choices=Mock()),runner=Mock(),show_error=Mock())
        adw=NS(MessageDialog=NS(new=Mock(return_value=dialog)))
        entry=Mock(); button=Mock(); choose_adept_profile(window,entry,button,Adw=adw)
        done=window.runner.submit.call_args.args[2]; done([{'reference':'stable','name':'hb'}]); entry.set_text.assert_not_called()
        callback=dialog.connect.call_args.args[1]; callback(dialog,'cancel'); entry.set_text.assert_not_called()
        callback(dialog,'profile-0'); entry.set_text.assert_called_once_with('stable')
        window.service.adept_profile_choices.assert_not_called()
    def test_empty_chooser_reports_action_without_selection(self):
        from cifereca.gui.settings_chooser_controller import choose_adept_profile
        window=NS(service=NS(adept_profile_choices=Mock()),runner=Mock(),show_error=Mock())
        entry=Mock();choose_adept_profile(window,entry,Mock(),Adw=None);window.runner.submit.call_args.args[2]([])
        window.show_error.assert_called_once();entry.set_text.assert_not_called()

    def test_token_expiration_precedes_cache_ttl(self):
        self.path.write_text('<fulfillmentToken><expiration>2026-10-03T12:00:00Z</expiration></fulfillmentToken>')
        self.assertTrue(self.remember(now=1791028790))
        self.assertEqual(self.reuse(now=1791028799),self.path)
        self.assertIsNone(self.reuse(now=1791028800))
    def test_invalid_token_expiration_not_remembered(self):
        self.path.write_text('<fulfillmentToken><expiration>bad</expiration></fulfillmentToken>');self.assertFalse(self.remember())
    def test_utf16_dtd_not_remembered(self):
        self.path.write_bytes('<!DOCTYPE x [<!ENTITY a "x">]><fulfillmentToken/>'.encode('utf-16'));self.assertFalse(self.remember())
    def test_runtime_failure_keeps_reusable_input(self):
        from cifereca.kobo.errors import KoboAdeptError
        self.connect_provider();self.service.kobo_profiles=Mock(return_value=[{'name':'hb','fetch_capable':True}])
        self.service.execute_acquisition_request=Mock(side_effect=KoboAdeptError('failure',code='kobo_adept_invalid_json'))
        for _ in range(2):
            # Malformed runtime JSON is a recoverable protocol failure rather
            # than a named Adobe license refusal or identity ambiguity.
            self.assertEqual(self.service._acquire_active_loan_automatically(self.loan)['status'],'retryable_error')
        self.session.download_digital.assert_called_once();self.assertEqual(self.service.execute_acquisition_request.call_count,2)
    def test_sync_issue_link_opens_identity_tab(self):
        from cifereca.gui.sync_issues_dialog_controller import show_sync_issues
        source=Path(__file__).resolve().parents[1]/'src/cifereca/gui/main_window.py'
        tree=ast.parse(source.read_text(encoding="utf-8"))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="MainWindow")
        facade=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=="_show_sync_issues")
        self.assertIn("sync_issues_dialog_controller.show_sync_issues",ast.unparse(facade))
        dialog=Mock();window=NS(_closed=False,navigate=Mock(),_settings_tabs=Mock())
        Adw=NS(MessageDialog=NS(new=Mock(return_value=dialog)))
        show_sync_issues(window,{'sync':{}},Adw=Adw)
        callback=dialog.connect.call_args.args[1]
        callback(dialog,'close');window.navigate.assert_not_called()
        callback(dialog,'settings');window.navigate.assert_called_once_with('settings')
        window._settings_tabs.set_visible_child_name.assert_called_once_with('accounts')
    def test_manual_loan_recovery_forces_provider_refresh(self):
        source=Path(__file__).resolve().parents[1]/'src/cifereca/gui/main_window.py'
        fn=next(n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.FunctionDef) and n.name=='prepare_pnb_for_loan')
        fn.args.args[1].annotation=None;fn.args.args[3].annotation=None;fn.returns=None
        from cifereca.task_queue import TaskPriority
        window=NS(service=NS(reacquire_digital_loan=Mock(return_value={"status":"complete"})),runner=Mock())
        ns={'TaskPriority':TaskPriority};exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(source),'exec'),ns)
        ns['prepare_pnb_for_loan'](window,self.loan);window.runner.submit.call_args.args[1]()
        window.service.reacquire_digital_loan.assert_called_once_with(self.loan)
    def test_opening_sync_twice_preserves_staged_acsm_without_runtime_attempt(self):
        self.connect_provider()
        self.service._loans_for_account=Mock(return_value=[self.loan])
        self.service.kobo_profiles=Mock(return_value=[{'name':'hb','fetch_capable':True},{'name':'heloise','fetch_capable':True}])
        self.service._kobo_adapter=Mock(return_value=NS(available=lambda:False))
        self.service.kobo_backup_enabled=Mock(return_value=False)
        self.service.catalog_scan=Mock(return_value={'scanned':0})
        self.service.execute_acquisition_request=Mock()
        for _ in range(2):
            enable_synthetic_paris_opening(self.service)
            summary=self.service.opening_sync()
            row=next(row for row in summary['identities'] if row['identity_id']=='default')
            self.assertEqual(row['attention_required'],1)
            self.assertEqual(self.service.storage.latest_sync_run('default')['detail']['acquisition_errors'][0]['code'],'adept_profile_ambiguous')
        self.session.download_digital.assert_called_once();self.service.execute_acquisition_request.assert_not_called()
