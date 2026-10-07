"""Regressions found when restoring the 0.9.2 test/installation context."""
import ast
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
import zipfile
from cifereca.core import CiferecaCore
from cifereca.diagnostics import Diagnostics
from cifereca.gui.shell_model import page_actions, resolve_pages
from cifereca.plugin_api import UiPageContribution
from cifereca.plugins import PackageStore, PluginConfigStore, PluginManager, PluginStateStore
from cifereca.providers import ProviderConfigurationError


class Consolidation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
        self.manager=PluginManager(package_store=PackageStore(self.path/'packages'),
            state_store=PluginStateStore(self.path/'state.json'),config_store=PluginConfigStore(self.path/'plugin-config.json'),
            diagnostics=Diagnostics(level="verbose"))
    def tearDown(self): self.manager.close();self.tmp.cleanup()
    def install_pages(self,source):
        p=self.path/'pages.zip'
        manifest={'manifest':1,'package':{'id':'test.pages','version':'1.0.0','publisher':'test'},
                  'plugins':[{'id':'test.pages','type':'ui.page','entrypoint':'test_consolidation_pages:Pages'}]}
        with zipfile.ZipFile(p,'w') as z:
            z.writestr('cifereca-plugin.json',json.dumps(manifest));z.writestr('test_consolidation_pages.py',source)
        self.manager.install(p)
    def test_standalone_manager_has_no_phantom_packages(self):
        self.assertEqual(self.manager.activate(),[])
        self.assertEqual(self.manager.package_inventory(),[])
        self.assertEqual(self.manager.page_contributions(),[])
    def test_core_explicitly_registers_standard_ui(self):
        with CiferecaCore.open(config_path=self.path/'config.json',database_path=self.path/'db') as core:
            self.assertEqual({s.descriptor.type for s in core.plugins.statuses()},{'ui.page','ui.widgets','ui.titlebar','maintenance.tool'})
    def test_bad_order_isolated_from_valid_page(self):
        self.install_pages('''from cifereca.plugin_api import UiPageContribution
class Pages:
 def activate(self, ctx): return self
 def pages(self):
  return [UiPageContribution('broken','Broken',order='bad',factory=lambda host: None), UiPageContribution('good','Good',factory=lambda host: None)]
''')
        self.assertEqual([p.route for p in self.manager.page_contributions()],['good'])
        self.assertEqual(self.manager.diagnostics.snapshot()[-1]['status'],'error')
    def test_wrong_contribution_isolated(self):
        self.install_pages('''from cifereca.plugin_api import UiPageContribution
class Pages:
 def activate(self, ctx): return self
 def pages(self): return [None, 'wrong', UiPageContribution('good','Good',factory=lambda host: None)]
''')
        self.assertEqual([p.route for p in self.manager.page_contributions()],['good'])
    def test_failing_page_provider_isolated(self):
        self.install_pages('''class Pages:
 def activate(self, ctx): return self
 def pages(self): raise RuntimeError('broken pages')
''')
        self.assertEqual(self.manager.page_contributions(),[])
        self.assertEqual(self.manager.status('test.pages').state,'ready')
    def test_invalid_action_does_not_break_default_open(self):
        page=UiPageContribution('x','X',factory=lambda host: None,actions=(None,'bad'))
        self.assertEqual([a.id for a in page_actions(page)],['open'])
    def test_null_actions_preserve_default_open(self):
        page=UiPageContribution('x','X',factory=lambda host: None,actions=None)
        self.assertEqual([a.id for a in page_actions(page)],['open'])
    def test_default_shell_ignores_wrong_page_objects(self):
        self.assertEqual(resolve_pages([None,'bad']),[])


class ProviderActivation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();p=Path(self.tmp.name)
        self.core=CiferecaCore.open(config_path=p/'config.json',database_path=p/'db')
        self.library=self.core.library
        self.status=NS(enabled=False,state='disabled',descriptor=NS(id='provider.test',instantiation='multiple'))
        self.manager=NS(status=Mock(side_effect=lambda _:self.status),enabled_for_identity=Mock(return_value=False))
        self.library._plugin_manager=self.manager
        self.library._provider_plugin_ids['test']='provider.test'
        self.session=NS(provider_id='test',connected=True,capabilities=NS(search=True),descriptor=NS(display_name='Test'),search=Mock(),close=Mock())
        self.library._sessions['test::credential']=self.session
    def tearDown(self): self.core.close();self.tmp.cleanup()
    def test_disabled_provider_disappears_from_search_choices(self): self.assertEqual(self.library.searchable_providers(),[])
    def test_disabled_provider_receives_no_online_request(self):
        self.assertEqual(self.library.search('dumas',force=True),[]);self.session.search.assert_not_called()
    def test_removed_plugin_receives_no_online_request(self):
        self.manager.status.side_effect=KeyError('uninstalled')
        self.assertEqual(self.library.search('dumas',force=True),[]);self.session.search.assert_not_called()
    def test_disabled_provider_cannot_resolve_live_download_session(self):
        with self.assertRaises(ProviderConfigurationError):self.library._session_for_source('test','credential')
    def test_ready_nonpublic_provider_remains_eligible(self):
        self.status.enabled=True;self.status.state='ready'
        self.assertEqual(self.library.searchable_providers(),[('test','Test')])
    def test_manually_registered_provider_preserves_compatibility(self):
        self.library._provider_plugin_ids.clear()
        self.assertEqual(self.library.searchable_providers(),[('test','Test')])
    def test_per_identity_public_activation_still_applies(self):
        self.status.enabled=True;self.status.state='ready';self.status.descriptor.instantiation='singleton_public'
        self.assertEqual(self.library.searchable_providers(),[])

    def test_failed_public_activation_check_receives_no_online_request(self):
        self.status.enabled=True;self.status.state='ready';self.status.descriptor.instantiation='singleton_public'
        self.manager.enabled_for_identity.side_effect=RuntimeError('configuration unavailable')
        self.assertEqual(self.library.search('dumas',force=True),[])
        self.session.search.assert_not_called()


class Selection(unittest.TestCase):
    def test_clearing_selection_clears_details_before_notifying_page(self):
        source=Path(__file__).resolve().parents[1]/'src/cifereca/gui/main_window.py'
        tree=ast.parse(source.read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MasterDetailPage')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_selection_changed')
        module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),method],type_ignores=[])
        ns={'BookListItem':type('BookListItem',(),{})};exec(compile(ast.fix_missing_locations(module),str(source),'exec'),ns)
        calls=[]
        page=NS(selection=NS(get_selected_item=lambda:None),detail=NS(empty=lambda *args:calls.append('clear')), _selected_callbacks=[lambda selected:calls.append(selected)], _narrow_master_detail=False)
        ns['_selection_changed'](page,None,None)
        self.assertEqual(calls,['clear',None])

if __name__=='__main__':unittest.main()
