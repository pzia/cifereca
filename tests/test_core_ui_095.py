from tests.gui_source import gui_source_text

import ast
import logging
import os
from pathlib import Path
import shutil
import zipfile
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

# No user state or credentials are loaded by this suite.
_session = tempfile.TemporaryDirectory()
for key in ('XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','XDG_STATE_HOME'):
    os.environ[key] = str(Path(_session.name) / key)

from cifereca.core import CiferecaCore
from cifereca.core.search import KnownCatalogSearch
from cifereca.diagnostics import Diagnostics, redact, redact_text
from cifereca.logging_utils import RedactionFilter
from cifereca.gui.query_model import QueryState, online_search, progressive_online_search, merge_results, result_row
from cifereca.gui.shell_model import resolve_pages, page_actions, visible_actions
from cifereca.plugin_api import UiPageContribution, UiActionContribution, write_bundle_manifest, load_bundle_manifest
from cifereca.models import SearchResult


def book(provider='paris', id='same', author='Alexandre Dumas', format='epub', **kw):
    return SearchResult(id,id,'DILICOM','digital','Les Trois Mousquetaires',author,None,format,None,None,None,None,None,'EBOOKS',source_provider=provider,**kw)


class Filters(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(key='a',title='Trois Mousquetaires',author='Alexandre Dumas',provider_id='paris',media_format='epub',metadata={'series':'D Artagnan','collection':'Classiques'}),
                     dict(key='b',title='Vingt ans après',author='Alexandre Dumas',provider_id='gallica',media_format='mp3',metadata={'bundle_members':[{'title':'Le Retour secret'}]}),
                     dict(key='c',title='Autre livre',author='Autre auteur',provider_id='gutenberg',media_format='pdf')]
    def test_author(self): self.assertEqual(len(QueryState('dumas',field='author').filter(self.rows)),2)
    def test_multi_provider(self): self.assertEqual(len(QueryState(providers=('paris','gallica')).filter(self.rows)),2)
    def test_audio(self): self.assertEqual(QueryState(types=('audiobook',)).filter(self.rows)[0]['key'],'b')
    def test_formats(self): self.assertEqual(len(QueryState(types=('epub','pdf')).filter(self.rows)),2)
    def test_series(self): self.assertEqual(QueryState('artagnan',field='series').filter(self.rows)[0]['key'],'a')
    def test_collection(self): self.assertEqual(QueryState('classiques',field='collection').filter(self.rows)[0]['key'],'a')
    def test_track_search_parent(self): self.assertEqual(QueryState('retour secret').filter(self.rows)[0]['key'],'b')
    def test_clear_facets_restores_loaded(self):
        self.assertEqual(len(QueryState(providers=('paris',)).filter(self.rows)),1)
        self.assertEqual(len(QueryState().filter(self.rows)),3)
    def test_unicode_tokens(self): self.assertEqual(len(QueryState('apres vingt',field='title').filter(self.rows)),1)
    def test_physical_scope(self): self.assertEqual(QueryState(scope='physical').filter(self.rows),[])
    def test_source_collection_is_not_editorial_collection(self):
        self.assertEqual(QueryState('library',field='collection').filter([{'title':'X','metadata':{'source_collection':'library'}}]),[])
    def test_input_unchanged(self):
        before=repr(self.rows);QueryState('dumas').filter(self.rows);self.assertEqual(repr(self.rows),before)


class Online(unittest.TestCase):
    def test_multi_provider_current_constraints(self):
        service=NS(search=Mock(return_value=[book()]),last_search_warnings=['partial'])
        rows,warnings=online_search(service,QueryState('dumas','author','digital',('paris','gallica'),('epub',)))
        self.assertEqual(service.search.call_count,2)
        self.assertEqual(service.search.call_args.kwargs,dict(force=True,provider_id='gallica',field='author',content_types=('epub',)))
        self.assertEqual(warnings,['partial','partial'])
    def test_local_selection_no_network(self):
        service=NS(search=Mock())
        self.assertEqual(online_search(service,QueryState('dumas',providers=('local',))),([],[]))
        service.search.assert_not_called()
    def test_all(self):
        service=NS(search=Mock(return_value=[]))
        online_search(service,QueryState('dumas'))
        self.assertEqual(service.search.call_args.kwargs['provider_id'],'all')
    def test_merge_cross_provider_ids_and_replace(self):
        updated=book(author='Updated')
        rows=merge_results([book(),book('gallica')],[updated])
        self.assertEqual(len(rows),2); self.assertIs(rows[0],updated)
    def test_result_format_mapping(self): self.assertEqual(result_row(book())['media_format'],'epub')


class Shell(unittest.TestCase):
    def test_old_positional_page_contract(self):
        p=UiPageContribution('x','X','icon',7,lambda host:object())
        self.assertEqual(p.group,'usage');self.assertEqual(p.actions,())
    def test_default_open(self):
        page=UiPageContribution('x','X',factory=lambda _:None)
        host=NS(navigate=Mock());page_actions(page)[0].callback(host)
        host.navigate.assert_called_once_with('x')
    def test_priority_override(self):
        old=UiPageContribution('x','Old',factory=lambda _:None,priority=-100)
        new=UiPageContribution('x','New',factory=lambda _:None,priority=1)
        self.assertEqual(resolve_pages([old,new])[0],new)
    def test_groups_from_metadata(self):
        pages=[UiPageContribution('x','X',factory=lambda _:None),UiPageContribution('s','S',factory=lambda _:None,group='configuration')]
        self.assertEqual(resolve_pages(pages)[0].group,'usage')
    def test_contextual_actions(self):
        page=UiPageContribution('x','X',factory=lambda _:None,actions=(UiActionContribution('refresh','R',lambda h:None),))
        self.assertEqual(visible_actions([page],'other',['x.refresh']),[])
        self.assertEqual(len(visible_actions([page],'x',['x.refresh'])),1)
    def test_open_always_global(self):
        page=UiPageContribution('x','X',factory=lambda _:None)
        self.assertEqual(visible_actions([page],'other',['x.open'])[0][0],'x.open')
    def test_invalid_factory_ignored(self): self.assertEqual(resolve_pages([UiPageContribution('x','X')]),[])


class DiagnosticsRawCompatibility(unittest.TestCase):
    def test_nested_original_values(self):
        data = {'nested': [{'token': 'secret', 'password': 'visible'}]}
        self.assertIs(redact(data), data)

    def test_signed_url_kept(self):
        value = 'Failure https://server.test/file.acsm?auth=SECRET'
        self.assertEqual(redact_text(value), value)

    def test_pnb_path_kept(self):
        value = 'https://pnb.test/v3/LOAN/SECRET/123.do'
        self.assertEqual(redact_text(value), value)

    def test_bearer_kept(self):
        value = 'Authorization: Bearer SECRET'
        self.assertEqual(redact_text(value), value)

    def test_url_credentials_kept(self):
        value = 'https://user:SECRET@host.test/file'
        self.assertEqual(redact_text(value), value)

    def test_levels(self):
        d=Diagnostics(level='verbose');d.add('x','normal');d.add('x','detail',level='detailed');d.add('x','trace',level='trace')
        self.assertEqual(len(d.snapshot()),2)
        d=Diagnostics(level='trace');d.add('x','trace',level='trace');self.assertEqual(len(d.snapshot()),1)

    def test_log_filter(self):
        record=logging.LogRecord('test',20,'',1,'Failed %s',('https://x.test/foo?token=SECRET',),None)
        RedactionFilter().filter(record);self.assertIn('SECRET',record.getMessage())

    def test_log_dict_secret_keys(self):
        record=logging.LogRecord('test',20,'',1,'Payload %s',({'api_token':'SECRET','normal':'visible'},),None)
        RedactionFilter().filter(record);self.assertIn('SECRET',record.getMessage())

    def test_normal_url_preserved(self):
        self.assertEqual(redact_text('https://host.test/book'),'https://host.test/book')


class CoreCompatibility(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.core=CiferecaCore.open(config_path=self.path/'config.json',database_path=self.path/'db.sqlite')
        self.iid=self.core.library.current_identity.identity_id
    def tearDown(self): self.core.close();self.tmp.cleanup()
    def test_schema9(self): self.assertEqual(self.core.storage.schema_version(),14)
    def test_builtin_plugins_activate_headless(self):
        self.assertEqual({s.state for s in self.core.plugins.statuses()},{'ready'})
        self.assertEqual(len(self.core.plugins.page_contributions()),12)
    def test_titlebar_persistence(self):
        self.core.plugins.set_plugin_config('core.ui.titlebar',{'actions':['search.open']})
        self.assertEqual(self.core.plugins.service('core.ui.titlebar').selected_actions(),['search.open'])
    def test_known_catalog_not_library_and_no_network(self):
        self.core.storage.catalog_entry_put({'catalog_entry_id':'cat','title':'Dumas','author':'Alexandre Dumas','identity_id':self.iid,'provider_id':'gallica','item_id':'1','local_state':'remote_catalog','media_format':'epub','metadata':{'series':'Cycle'}})
        self.core.library.search=Mock(side_effect=AssertionError('NETWORK'))
        self.assertEqual(len(self.core.library.search_known_catalog('dumas')),1)
        self.assertEqual(self.core.library.library_entries(),[])
        self.core.library.search.assert_not_called()
    def test_active_entitlement_library(self):
        self.core.storage.entitlement_put({'provider_id':'paris','credential_id':'test','identity_id':self.iid,'item_id':'1','entitlement_type':'loan','state':'active','metadata':{'title':'Dumas','author':'Alexandre Dumas','media_format':'epub'}})
        self.assertEqual(len(self.core.library.library_entries()),1)
    def test_identity_scoping_known(self):
        self.core.storage.catalog_entry_put({'catalog_entry_id':'cat','title':'Dumas','identity_id':'other','provider_id':'paris','item_id':'1','local_state':'remote_owned'})
        self.assertEqual(self.core.library.search_known_catalog('dumas'),[])
    def test_bundle_copied_independently(self):
        bundle=self.path/'bundle';bundle.mkdir();(bundle/'track.mp3').write_bytes(b'test audio')
        write_bundle_manifest(bundle,title='Audio Dumas',member_metadata={'track.mp3':{'title':'Retour secret'}})
        copied=self.path/'copied';shutil.copytree(bundle,copied)
        self.assertEqual(load_bundle_manifest(copied).title,'Audio Dumas')
    def test_acsm_remediable_preserves_original_and_staging(self):
        from cifereca.identities.resolver import AdeptProfileResolutionError
        source=self.path/'original.acsm';source.write_text('<fulfillment/>')
        self.core.library.resolve_adept_profile_for_identity=Mock(side_effect=AdeptProfileResolutionError('Configure profile'))
        result=self.core.library.import_acsm(source,identity_id=self.iid)
        self.assertEqual(result['status'],'attention_required')
        self.assertTrue(source.is_file());self.assertTrue(Path(result['staging']).is_file())
    def test_acsm_mock_fulfill_idempotence_and_original_preserved(self):
        source=self.path/'original.acsm';source.write_text('<fulfillment><format>application/pdf</format></fulfillment>')
        directory=self.path/'library'/'manual';directory.mkdir(parents=True)
        self.core.library.library_identity_provider_dir=Mock(return_value=directory)
        self.core.library.library_dir=Mock(return_value=self.path/'library')
        self.core.library.resolve_adept_profile_for_identity=Mock(return_value=('cli-profile','identity'))
        def fulfill(request):
            self.assertEqual(request['profile'],'cli-profile')
            artifact=Path(request['destination']);artifact.write_bytes(b'%PDF-1.4\n%%EOF\n')
            return {'ok':True,'operation':'download','artifact':{'path':str(artifact)}}
        self.core.library.execute_acquisition_request=Mock(side_effect=fulfill)
        first=self.core.library.import_acsm(source,identity_id=self.iid)
        self.assertEqual(first['status'],'complete');self.assertTrue(source.is_file())
        second=self.core.library.import_acsm(source,identity_id=self.iid)
        self.assertEqual(second['status'],'unchanged')
        self.core.library.execute_acquisition_request.assert_called_once()
    def test_plugins094_manifest_and_activation(self):
        archives=Path(os.environ.get('CIFERECA_TEST_PLUGIN_DIR',str(Path(__file__).resolve().parents[1]/'tests/fixtures/plugins-0.9.4')))
        if not archives.exists(): self.skipTest('Supply plugin archives in sources/plugins for integration test')
        for archive in archives.glob('*.zip'):
            self.core.plugins.install(archive)
        rows=self.core.plugins.statuses()
        self.assertFalse([s for s in rows if s.state in {'error','incompatible'}],[(s.descriptor.id,s.state,s.error) for s in rows])
        self.assertGreater(len(rows),7)
    def test_public_identity_activation(self):
        archives=Path(os.environ.get('CIFERECA_TEST_PLUGIN_DIR',str(Path(__file__).resolve().parents[1]/'tests/fixtures/plugins-0.9.4')))
        archive=archives/'cifereca-plugin-provider-gutenberg-1.1.0.zip'
        if not archive.exists(): self.skipTest('Supply plugin archive')
        self.core.plugins.install(archive)
        self.core.plugins.set_plugin_config('provider.gutenberg', {'contact':'test@example.invalid'})
        plugin=next(s.descriptor.id for s in self.core.plugins.statuses() if s.descriptor.instantiation=='singleton_public')
        self.core.plugins.set_identity_enabled(plugin,self.iid,False)
        self.assertFalse(self.core.plugins.enabled_for_identity(plugin,self.iid))
        self.assertTrue(self.core.plugins.enabled_for_identity(plugin,'other'))


# Execute the production window's coordinator methods without importing GTK.
# This validates callbacks and network decisions, not rendering or GTK APIs.
def window_coordinator():
    source=Path(__file__).resolve().parents[1]/'src/cifereca/gui/main_window.py'
    tree=ast.parse(gui_source_text(source))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MainWindow')
    names={'run_search','_library_search_online','_reset_search_enter'}
    methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in names]
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),ast.ClassDef(name='Coordinator',bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[])
    namespace=dict(__name__='cifereca.gui.test_search_ast',__package__='cifereca.gui',online_search=online_search,progressive_online_search=progressive_online_search,merge_results=merge_results,TaskPriority=NS(USER_ACTION=0))
    exec(compile(ast.fix_missing_locations(module),str(source),'exec'),namespace)
    return namespace['Coordinator']()

class SearchCoordinator(unittest.TestCase):
    def setUp(self):
        self.window=window_coordinator();w=self.window
        w.search_controls=NS(state=lambda:QueryState('dumas'))
        w.library_controls=NS(state=lambda:QueryState('dumas','author',providers=('paris',)))
        w.search_controls.set_state=Mock()
        w.service=NS(current_identity=NS(identity_id='a'),search_known_catalog=Mock(return_value=[]),search=Mock(return_value=[]),search_progressive=Mock(return_value=NS(warnings=())),last_search=[],last_search_warnings=[])
        self.tasks=[]
        w.runner=NS(submit=lambda *args,**kw:self.tasks.append(args))
        w._search_generation=0;w._last_search_query='';w._search_mode='local';w._search_local_objects={};w._remote_search_results=[]
        w.search_button=object();w.search_global_button=object()
        w._populate_local_search=Mock();w._populate_search=Mock();w.toast=Mock();w.navigate=Mock()
    def test_known_calls_only_known(self):
        w=self.window;w.run_search(False);self.tasks[0][2](self.tasks[0][1]())
        w.service.search.assert_not_called();w.service.search_known_catalog.assert_called_once()
    def test_transfer_never_online(self):
        w=self.window;w._library_search_online(None);self.tasks[0][1]()
        w.service.search.assert_not_called();w.navigate.assert_called_once_with('search')
    def test_stale_search_callback_ignored(self):
        w=self.window;w.run_search(False);w.run_search(False)
        self.tasks[0][2]([{'title':'stale'}]);w._populate_local_search.assert_not_called()
    def test_identity_change_callback_ignored(self):
        w=self.window;w.run_search(False);w.service.current_identity.identity_id='b'
        self.tasks[0][2]([]);w._populate_local_search.assert_not_called()
    def test_online_explicit(self):
        w=self.window;w.run_search(True);self.tasks[0][2](self.tasks[0][1]())
        w.service.search_progressive.assert_called_once();w._populate_search.assert_called_once()
    def test_short_query(self):
        w=self.window;w.search_controls.state=lambda:QueryState('a');w.run_search(True)
        self.assertEqual(self.tasks,[]);w.toast.assert_called_once()

if __name__=='__main__': unittest.main()
