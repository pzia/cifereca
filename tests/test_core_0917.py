from tests.gui_source import gui_source_text

import ast, hashlib, unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from cifereca.core.search import filter_loaded_results, available_formats
from cifereca.gui.query_model import QueryState, apply_facet
from cifereca.gui.search_intent import SearchEnterSequence
from cifereca.adept_binding import capture_binding
from cifereca.plugin_api import AttentionRequired
from cifereca.providers import ProviderConfigurationError
from . import test_core_0915 as fixture
from . import test_core_ui_095 as coordinator_fixture

class Core0917(unittest.TestCase):
    setUp = fixture.Core0915.setUp
    tearDown = fixture.Core0915.tearDown
    book = fixture.Core0915.book

    def test_enter_twice_without_automatic_reset(self):
        seq=SearchEnterSequence(); state=QueryState('harari')
        self.assertFalse(seq.press(state)); self.assertTrue(seq.press(state))
        self.assertFalse(seq.press(state))
    def test_other_interaction_resets_enter(self):
        seq=SearchEnterSequence();state=QueryState('harari');seq.press(state);seq.reset()
        self.assertFalse(seq.press(state))
    def test_filter_change_resets_sequence(self):
        seq=SearchEnterSequence();seq.press(QueryState('harari'))
        self.assertFalse(seq.press(QueryState('harari',types=('pdf',))))
    def test_short_text_never_arms_online(self):
        seq=SearchEnterSequence();self.assertFalse(seq.press(QueryState('a')));self.assertFalse(seq.press(QueryState('a')))
    def test_online_same_request_in_flight_is_deduplicated(self):
        w=coordinator_fixture.window_coordinator(); tasks=[]
        w.search_controls=NS(state=lambda:QueryState('harari'))
        w.service=NS(current_identity=NS(identity_id='i'));w.runner=NS(submit=lambda *args,**kw:tasks.append(args))
        w._search_generation=0;w._last_search_query='';w._remote_search_results=[]
        w.search_global_button=object();w.run_search(True);generation=w._search_generation;w.run_search(True)
        self.assertEqual(len(tasks),1);self.assertEqual(w._search_generation,generation)
        w.show_error=Mock();tasks[0][3](ValueError('offline'))
        self.assertIsNone(w._online_pending_key);w.run_search(True);self.assertEqual(len(tasks),2)
    def test_format_facet_preserves_text_field_sources(self):
        state=QueryState('Dumas','author','digital',('paris',),('pdf',))
        self.assertEqual(apply_facet(state,'format','KEPUB'),QueryState('Dumas','author','digital',('paris',),('epub',)))
    def test_source_facet_uses_id_not_display_label(self):
        state=QueryState('Dumas',types=('epub',))
        self.assertEqual(apply_facet(state,'source','gutenberg'),QueryState('Dumas',providers=('gutenberg',),types=('epub',)))
    def test_unknown_format_is_not_a_filter(self):
        state=QueryState('Dumas');self.assertEqual(apply_facet(state,'format','MONO'),state)
    def test_author_text_normalized(self):
        state=apply_facet(QueryState(),'author','Auteur : Harari, Yuval Noah')
        self.assertEqual((state.field,state.text),('author','Harari Yuval Noah'))
    def test_remote_multiple_formats_match_each(self):
        row={'title':'Livre','format':'MONO','metadata':{'formats':['epub','pdf','MONE']}}
        self.assertEqual(available_formats(row),('epub','pdf'))
        for fmt in ('epub','pdf'): self.assertEqual(len(filter_loaded_results([row],content_types=[fmt])),1)
        self.assertEqual(filter_loaded_results([row],content_types=['mp3']),[])
    def test_local_observed_format_dominates_remote_alternatives(self):
        row={'local':True,'media_format':'pdf','metadata':{'formats':['epub','pdf']}}
        self.assertEqual(filter_loaded_results([row],content_types=['epub']),[])
    def test_kepub_matches_epub_family(self):
        self.assertEqual(len(filter_loaded_results([{'format':'kepub'}],content_types=['epub'])),1)
    def test_audio_multiformats_and_unknown(self):
        self.assertEqual(available_formats({'metadata':{'audio_formats':['audio/mpeg','ogg']}}),('mp3','ogg'))
        self.assertEqual(available_formats({'format':'EXTL'}),())
    def test_observed_backup_pdf_with_epub_suffix(self):
        path=self.base/'misnamed.epub';path.write_bytes(b'%PDF-1.4\n%%EOF\n')
        observed=self.service._observed_backup_artifact(path)
        self.assertEqual(observed['format'],'PDF');self.assertEqual(observed['sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
    def test_backup_does_not_trust_extension(self):
        path=self.base/'wrong.epub';path.write_bytes(b'PK\x03\x04invalid')
        with self.assertRaises(ProviderConfigurationError):self.service._observed_backup_artifact(path)
    def test_backup_rejects_remaining_content_encryption(self):
        import zipfile
        path=self.book('old')
        with zipfile.ZipFile(path,'a') as z:z.writestr('META-INF/encryption.xml','<aes xmlns="http://www.w3.org/2001/04/xmlenc#"/>')
        with self.assertRaises(ProviderConfigurationError):self.service._observed_backup_artifact(path)
    def http(self,source,extra=None):
        self.service._plugin_manager=object();transport=NS(fetch=Mock(),matches=Mock(return_value=True))
        self.service._required_plugin_service=Mock(return_value=transport)
        request={'source':{'type':'http','url':'https://example.org/book',**source},'destination':str(self.base/'book.epub'),**(extra or {})}
        return request,transport
    def test_http_rejects_explicit_unknown_and_protected_drm_before_fetch(self):
        for drm in ['unknown','kdrm','adobe',None]:
            request,transport=self.http({'drm':drm})
            with self.assertRaises(AttentionRequired):self.service.execute_acquisition_request(request)
            transport.fetch.assert_not_called()
    def test_http_rejects_request_level_protection(self):
        request,transport=self.http({'drm':'none'},{'drm':'adobe'})
        with self.assertRaises(AttentionRequired):self.service.execute_acquisition_request(request)
        transport.fetch.assert_not_called()
    def test_http_rejects_dedicated_handling(self):
        request,transport=self.http({'handling':'acsm_adept'})
        with self.assertRaises(AttentionRequired):self.service.execute_acquisition_request(request)
        transport.fetch.assert_not_called()
    def test_http_preserves_declared_policy_and_signed_source(self):
        path=self.book('old');request,transport=self.http({'drm':'SignedNoDrm','handling':'http_plain','headers':{'x-header':'v'}})
        transport.fetch.return_value=path
        result=self.service.execute_acquisition_request(request)
        self.assertTrue(result['ok']);source=transport.fetch.call_args.args[0]
        self.assertEqual(source['drm'],'SignedNoDrm');self.assertEqual(source['handling'],'http_plain');self.assertEqual(source['headers'],{'x-header':'v'})
        self.assertEqual(source['protection'],{'handling':'http_plain','drm':'SignedNoDrm','protected':False,'attested':True})
    def test_http_contract_without_policy_is_rejected_before_transport(self):
        request,transport=self.http({})
        with self.assertRaises(AttentionRequired) as ctx:self.service.execute_acquisition_request(request)
        self.assertIn(ctx.exception.code,{'http_handling_attestation_required','http_drm_attestation_required'})
        transport.fetch.assert_not_called()
    def test_http_accepts_structured_unprotected_attestation(self):
        path=self.book('old');request,transport=self.http({'handling':'http_plain','drm':{'protected':False,'family':'none'}})
        transport.fetch.return_value=path
        self.assertTrue(self.service.execute_acquisition_request(request)['ok'])
        self.assertFalse(transport.fetch.call_args.args[0]['protection']['protected'])
    def test_http_respects_transport_refusal(self):
        request,transport=self.http({'drm':'none','handling':'http_plain'});transport.matches.return_value=False
        with self.assertRaises(AttentionRequired):self.service.execute_acquisition_request(request)
        transport.fetch.assert_not_called()
    def test_binding_captures_exact_runtime_id(self):
        b=capture_binding({'ok':True,'profile':'hb','loan_id':'urn:uuid:ade'},profile_name='hb',profile_id='stable')
        self.assertEqual(b['adept_loan_id'],'urn:uuid:ade');self.assertTrue(b['return_available'])
    def test_missing_adept_id_never_substitutes_provider_id(self):
        b=capture_binding({'ok':True,'metadata':{'loan_id':'provider-id'}},profile_name='hb',profile_id='stable')
        self.assertIsNone(b['adept_loan_id']);self.assertFalse(b['return_available'])
    def test_binding_profile_mismatch_and_failure_rejected(self):
        for result in [{'ok':False},{'ok':True,'profile':'other'},{'ok':True,'loan_id':123}]:
            with self.assertRaises(ValueError):capture_binding(result,profile_name='hb',profile_id='stable')
    def returned_fixture(self):
        self.service._adept_return_capability=Mock(return_value=True)
        path=self.book('old')
        b=capture_binding({'ok':True,'profile':'original','loan_id':'exact-adept'},profile_name='original',profile_id='stable-profile')
        aid=self.storage.acquisition_put({'identity_id':'stable','provider_id':'paris','credential_id':'c','item_id':'provider-id','status':'complete','metadata':{'path':str(path),'adept_binding':b}})
        self.service.kobo_profiles=Mock(return_value=[{'profile_id':'stable-profile','name':'renamed-profile'}])
        result={'ok':True,'operation':'return','metadata':{'runtime_result':{'ok':True,'operation':'loan_return','loan_id':'exact-adept','profile':'renamed-profile'}}}
        self.service.execute_drm_mutation=Mock(return_value=result)
        self.service._kobo_adapter=Mock(return_value=NS(loans=lambda name:{"loans":[{"id":"exact-adept","expired":False,"token_present":not bool(self.service.execute_drm_mutation.call_count),"returned":bool(self.service.execute_drm_mutation.call_count)}]}))
        return aid,path,result
    def test_return_resolves_original_profile_and_keeps_file(self):
        aid,path,result=self.returned_fixture();self.assertTrue(self.service.return_adept_acquisition(aid)['ok'])
        req=self.service.execute_drm_mutation.call_args.args[0]
        self.assertEqual(req.metadata['adept_loan_id'],'exact-adept');self.assertEqual(req.metadata['adept_profile_name'],'renamed-profile')
        self.assertTrue(path.is_file());self.assertEqual(self.storage.acquisition_get(aid)['metadata']['adept_binding']['loan_state'],'returned')
        self.service.return_adept_acquisition(aid);self.service.execute_drm_mutation.assert_called_once()
    def test_unconfirmed_return_leaves_state_and_file(self):
        aid,path,result=self.returned_fixture();result['metadata']['runtime_result']['loan_id']='other'
        with self.assertRaises(ProviderConfigurationError):self.service.return_adept_acquisition(aid)
        self.assertEqual(self.storage.acquisition_get(aid)['metadata']['adept_binding']['loan_state'],'active');self.assertTrue(path.is_file())
    def test_missing_origin_profile_prevents_return(self):
        aid,path,result=self.returned_fixture();self.service.kobo_profiles.return_value=[]
        with self.assertRaises(AttentionRequired):self.service.return_adept_acquisition(aid)
        self.service.execute_drm_mutation.assert_not_called()
    def test_builtin_inventory_has_actual_version(self):
        from cifereca.plugins.manager import PluginManager
        from cifereca import __version__
        manager=NS(packages=NS(all_packages=lambda:{}),_builtin_ui=True)
        self.assertEqual(PluginManager.package_inventory(manager)[0]['active'],__version__)

    def test_return_lookup_is_scoped_to_identity_and_artifact(self):
        aid,path,result=self.returned_fixture();row=self.storage.acquisition_get(aid)
        self.storage.acquisition_put({**row,'artifact_id':'sha256:artifact'})
        self.assertEqual(self.service.adept_return_for_artifact('sha256:artifact','stable')['acquisition_id'],aid)
        self.assertIsNone(self.service.adept_return_for_artifact('sha256:artifact','other'))
        self.assertIsNone(self.service.adept_return_for_artifact('sha256:other','stable'))
    def test_binding_origin_auto_profile_is_captured_without_changing_identity(self):
        self.service.kobo_profiles=Mock(return_value=[{'profile_id':'origin','name':'hb'}])
        self.assertEqual(self.service._originating_adept_profile_id('stable','hb'),'origin')
        self.assertIsNone(self.storage.adept_profile_binding_get('identity','stable'))
    def test_third_enter_while_online_running_does_not_invalidate_callback(self):
        source=Path(__file__).resolve().parents[1]/'src/cifereca/gui/main_window.py'
        fn=next(n for n in ast.walk(ast.parse(gui_source_text(source))) if isinstance(n,ast.FunctionDef) and n.name=='_search_enter')
        module=ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), fn],type_ignores=[]);ns={'__package__':'cifereca.gui'}
        exec(compile(ast.fix_missing_locations(module),str(source),'exec'),ns)
        state=QueryState('harari');w=NS(search_controls=NS(state=lambda:state),service=NS(current_identity=NS(identity_id='stable')),run_search=Mock(),_online_pending_key=(state,'stable'),_online_pending_generation=7,_search_generation=7)
        ns['_search_enter'](w);w.run_search.assert_not_called()

    def test_targeted_index_preserves_format_provenance_on_existing_artifact(self):
        from cifereca.acquisition_engine import Artifact
        path=self.book('old');digest=hashlib.sha256(path.read_bytes()).hexdigest()
        self.storage.artifact_put(Artifact.from_file(path,sha256=digest,media_format='epub',metadata={'announced_format':None,'preferred_format':'pdf','observed_format':'epub'}))
        self.service.catalog_index_path(path,title='Test',item_id='1',identity_id='stable',provider_id='kobo')
        meta=self.storage.artifact_get('sha256:'+digest)['metadata']
        self.assertEqual(meta['preferred_format'],'pdf');self.assertEqual(meta['observed_format'],'epub')
    def test_rescan_preserves_format_provenance(self):
        from cifereca.acquisition_engine import Artifact
        path=self.book('old');digest=hashlib.sha256(path.read_bytes()).hexdigest()
        self.storage.artifact_put(Artifact.from_file(path,sha256=digest,media_format='epub',metadata={'format_evidence':{'sha256':digest},'observed_format':'epub'}))
        self.service.catalog_scan()
        self.assertEqual(self.storage.artifact_get('sha256:'+digest)['metadata']['format_evidence']['sha256'],digest)
