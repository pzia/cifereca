"""Recovery of deleted local files and explicit ACSM fulfillment/return binding."""
from tests.gui_source import gui_source_text
import ast,tempfile,unittest,zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from cifereca.core import CiferecaCore
from cifereca.models import Loan, Account
from cifereca.acquisition import Acquisition
from cifereca.plugin_api import AttentionRequired
from cifereca.locking import LockBusyError
from cifereca.task_queue import TaskPriority
from cifereca.gui.loan_view import loan_due_detail_value

class Core0919(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.core=CiferecaCore.open(config_path=self.root/'config.json',database_path=self.root/'db')
  self.service=self.core.library;self.storage=self.core.storage
  self.service._adept_return_capability=Mock(return_value=True)
  self.core.config.set('library.directory',str(self.root/'EBooks'))
  self.loan=Loan(id='paris-loan',provider='DILICOM',medium='digital',record_id='book',title='Livre',source_provider='paris',source_credential='reader',source_account_id='account',identity_id='default',due_at='2099-01-01T00:00:00Z')
  self.storage.provider_connection_put({'identity_id':'default','provider_id':'paris','credential_id':'reader','label':'Paris'})
  self.service.accounts.append(Account(unique_id='account',provider_id='paris',credential_id='reader',identity_id='default'))
  self.storage.adept_profile_binding_set('identity','default','stable-profile')
  self.service.can_download_loan=Mock(return_value=True)
  self.service.kobo_profiles=Mock(return_value=[{'profile_id':'stable-profile','name':'hb','fetch_capable':True}])
  self.service.runtime_diagnostic=Mock(return_value={})
  self.acsm=self.root/'loan.acsm'
  def download(loan,**kw):
   self.acsm.write_text('<fulfillmentToken><format>application/epub+zip</format></fulfillmentToken>')
   return Acquisition(provider_id='paris',credential_id='reader',account_id='account',item_id='book',loan_id=loan.id,kind='acsm',source_url='https://test.invalid',local_path=self.acsm,mime_type='application/vnd.adobe.adept+xml',drm='adept',acsm_path=self.acsm,target_dir=self.root)
  self.service.acquire_digital_loan=Mock(side_effect=download)
  def execute(request):
   path=Path(request['destination']);self.book(path,b'new')
   return {'ok':True,'profile':'hb','loan_id':'adept-exact','artifact':{'path':str(path),'format':'epub','drm':'none'}}
  self.service.execute_acquisition_request=Mock(side_effect=execute)
  self.service._persist_active_loans('default',[self.loan])
 def tearDown(self):self.core.close();self.tmp.cleanup()
 def book(self,path,content=b'old'):
  path.parent.mkdir(parents=True,exist_ok=True)
  with zipfile.ZipFile(path,'w') as z:
   z.writestr('mimetype','application/epub+zip');z.writestr('META-INF/container.xml','<container/>');z.writestr('content.xhtml',content)
  return path
 def initial(self):
  result=self.service._acquire_active_loan_automatically(self.loan)
  self.assertEqual(result['status'],'complete');return Path(result['path'])
 def test_stale_catalog_after_deletion_does_not_skip_automatic_recovery(self):
  path=self.initial();path.unlink()
  result=self.service._acquire_active_loan_automatically(self.loan)
  self.assertEqual(result['status'],'complete');self.assertTrue(Path(result['path']).is_file())
  self.assertEqual(self.service.execute_acquisition_request.call_count,2)
 def test_automatic_existing_valid_file_keeps_skip_behavior(self):
  path=self.initial();before=path.read_bytes()
  self.assertEqual(self.service._acquire_active_loan_automatically(self.loan)['status'],'unchanged')
  self.assertEqual(self.service.execute_acquisition_request.call_count,1);self.assertEqual(path.read_bytes(),before)
 def test_manual_existing_file_is_preserved_and_new_file_has_binding(self):
  original=self.initial();before=original.read_bytes()
  result=self.service.reacquire_digital_loan(self.loan);new=Path(result['path'])
  self.assertNotEqual(original,new);self.assertEqual(original.read_bytes(),before);self.assertTrue(new.is_file())
  self.assertEqual(self.service.acquire_digital_loan.call_args.kwargs,{'force_refresh':True})
  status=self.service.adept_return_status_for_loan(self.loan)
  self.assertEqual(status['adept_loan_id'],'adept-exact');self.assertTrue(status['return_available'])
  self.assertEqual(self.storage.acquisition_get(status['acquisition_id'])['metadata']['preserved_artifact_path'],str(original))
 def test_manual_deleted_file_forces_complete_recovery(self):
  self.initial().unlink();result=self.service.reacquire_digital_loan(self.loan)
  self.assertTrue(Path(result['path']).is_file());self.assertTrue(result['adept_return_available'])
 def test_failed_reacquisition_preserves_file_and_previous_return_binding(self):
  original=self.initial();before=original.read_bytes();binding=self.service.adept_return_status_for_loan(self.loan)
  self.service.execute_acquisition_request.side_effect=AttentionRequired('Runtime refusé',code='runtime_refused')
  with self.assertRaises(AttentionRequired):self.service.reacquire_digital_loan(self.loan)
  self.assertEqual(original.read_bytes(),before)
  self.assertTrue(self.service.adept_return_status_for_loan(self.loan)['return_available'])
  self.assertEqual(self.storage.acquisition_get(binding['acquisition_id'])['metadata']['last_reacquisition_failure']['code'],'runtime_refused')
  self.assertTrue(self.acsm.exists())
 def test_single_loan_recovery_does_not_realize_other_active_loans(self):
  other=replace(self.loan,id='other-loan',record_id='other-book');self.service._persist_active_loans('default',[self.loan,other])
  self.service.reacquire_digital_loan(self.loan)
  active=self.storage.entitlements(provider_id='paris',state='active')
  self.assertEqual({r['loan_id'] for r in active},{self.loan.id,other.id})
 def test_missing_adept_id_is_visible_and_never_substituted_with_paris_id(self):
  execute=self.service.execute_acquisition_request.side_effect
  def no_id(request):result=execute(request);result.pop('loan_id');return result
  self.service.execute_acquisition_request.side_effect=no_id
  result=self.service.reacquire_digital_loan(self.loan);self.assertFalse(result['adept_return_available'])
  status=self.service.adept_return_status_for_loan(self.loan)
  self.assertEqual(status['status'],'loan_id_missing');self.assertIsNone(status['adept_loan_id'])
 def test_origin_profile_missing_is_visible(self):
  self.initial();status=self.service.adept_return_status_for_loan(self.loan);row=self.storage.acquisition_get(status['acquisition_id'])
  row['metadata']['adept_binding'].update(adept_profile_id=None,return_available=False);self.storage.acquisition_put(row)
  self.assertEqual(self.service.adept_return_status_for_loan(self.loan)['status'],'profile_missing')
 def test_binding_does_not_cross_credential_account_or_new_loan(self):
  self.initial()
  for change in [{'source_account_id':'other'},{'source_credential':'other'},{'id':'new-loan'}]:
   with self.subTest(change=change):self.assertFalse(self.service.adept_return_status_for_loan(replace(self.loan,**change))['return_available'])
 def test_no_acquisition_is_explicit(self):self.assertEqual(self.service.adept_return_status_for_loan(self.loan)['status'],'no_acquisition')
 def test_binding_log_distinguishes_provider_id_and_adept_presence(self):
  with self.assertLogs('cifereca',level='INFO') as logs:self.initial()
  text='\n'.join(logs.output);self.assertRegex(text,r'adept begin loan_ref=[0-9a-f]{20}')
  self.assertIn('adept_loan_id_present=True',text);self.assertIn('origin_profile_id_present=True',text)
 def test_title_collision_preserves_unrelated_file(self):
  path=self.service.library_identity_provider_dir('default','paris')/'Livre.epub';self.book(path,b'unrelated');before=path.read_bytes()
  result=self.service.reacquire_digital_loan(self.loan);self.assertNotEqual(result['path'],str(path));self.assertEqual(path.read_bytes(),before)
 def test_lock_busy_never_downloads(self):
  with patch('cifereca.services.InterProcessLock') as lock:
   lock.return_value.__enter__.side_effect=LockBusyError('busy')
   with self.assertRaises(AttentionRequired) as e:self.service.reacquire_digital_loan(self.loan)
  self.assertEqual(e.exception.code,'acquisition_busy');self.service.acquire_digital_loan.assert_not_called()
 def test_pnb_refusal_reports_plugin_version_without_inventing_protocol(self):
  self.service.acquire_digital_loan.side_effect=AttentionRequired('Refus acs4.edelivres.fr',code='pnb_redirect_refused')
  self.service._plugin_manager=NS(status=lambda pid:NS(descriptor=NS(version='1.2.4')))
  result=self.service._acquire_active_loan_automatically(self.loan)
  self.assertIn('1.2.4',result['error']);self.assertIn('target_scheme / target_port',result['error'])
 def extract(self,name,ns=None):
  path=Path(__file__).resolve().parents[1]/'src/cifereca/gui/main_window.py'
  fn=next(n for n in ast.walk(ast.parse(gui_source_text(path))) if isinstance(n,ast.FunctionDef) and n.name==name)
  for a in fn.args.args+fn.args.kwonlyargs:a.annotation=None
  fn.returns=None;out={'TaskPriority':TaskPriority,**(ns or {})};exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(path),'exec'),out);return out[name]
 def test_download_ui_calls_full_recovery_and_refreshes_binding(self):
  w=NS(service=NS(reacquire_digital_loan=Mock(return_value={'status':'complete','adept_return_available':True})),runner=Mock(),refresh_library=Mock(),refresh_recent=Mock(),_render_dashboard=Mock(),loan_page=NS(detail=NS(show_loan=Mock())),toast=Mock(),show_error=Mock())
  self.extract('prepare_pnb_for_loan')(w,self.loan);args=w.runner.submit.call_args.args
  result=args[1]();args[2](result);w.service.reacquire_digital_loan.assert_called_once_with(self.loan)
  w.loan_page.detail.show_loan.assert_called_once_with(self.loan);self.assertIn('restitution ADEPT disponible',w.toast.call_args.args[0])
 def test_return_ui_uses_exact_acquisition_and_keeps_file(self):
  w=NS(service=NS(return_adept_acquisition=Mock(return_value={'ok':True})),runner=Mock(),refresh_library=Mock(),toast=Mock(),show_error=Mock())
  self.extract('return_adept_from_detail')(w,'binding-id',Mock());args=w.runner.submit.call_args.args;args[2](args[1]())
  w.service.return_adept_acquisition.assert_called_once_with('binding-id');self.assertIn('fichiers conservés',w.toast.call_args.args[0])
 def test_loan_detail_displays_adept_id_and_wires_return_button(self):
  button=Mock();window=NS(service=NS(provider_display_name=lambda _provider:'Paris',can_download_loan=lambda loan:False,adept_return_status_for_loan=lambda loan:{'status':'ready','return_available':True,'acquisition_id':'binding-id','adept_loan_id':'adept-exact','profile_name':'hb'}),action_button=Mock(return_value=button),return_adept_from_detail=Mock(),config=NS(get=lambda key,default=None:default))
  detail=NS(_decorate=Mock(),_clear_actions=Mock(),window=window,content=Mock(),_cancel_meta_request=Mock(),_cover_and_title=Mock(),_group=Mock(),_actions=Mock(),show_presentation=Mock())
  self.extract('show_loan',{'clear_box':Mock(),'fr_date':lambda value:value,'loan_due_detail_value':loan_due_detail_value,'Gtk':NS(),'add_css':Mock(),'book_detail_presentation':lambda **kwargs:kwargs,'file_format':lambda value:str(value or '').lower(),'detail_language':lambda _raw:'','compact_availability_label':lambda value:value})(detail,self.loan)
  self.assertEqual(detail._group.call_args.args[0],'Restitution ADEPT')
  self.assertIn(('Identifiant ADEPT','adept-exact'),detail._group.call_args.args[1])
  button.connect.call_args.args[1](button);window.return_adept_from_detail.assert_called_once_with('binding-id',button)
 def test_recovery_with_other_actual_format_preserves_existing_pdf(self):
  old=self.service.library_identity_provider_dir('default','paris')/'Livre.pdf';old.parent.mkdir(parents=True,exist_ok=True);old.write_bytes(b'%PDF-1.7\noriginal');before=old.read_bytes()
  def produce(request):
   path=Path(request['destination']);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'%PDF-1.7\n1 0 obj <<>> endobj\n%%EOF')
   return {'ok':True,'profile':'hb','loan_id':'adept-exact','artifact':{'path':str(path),'format':'epub'}}
  self.service.execute_acquisition_request.side_effect=produce
  result=self.service.reacquire_digital_loan(self.loan);self.assertTrue(result['path'].endswith('.pdf'));self.assertNotEqual(result['path'],str(old));self.assertEqual(old.read_bytes(),before)
