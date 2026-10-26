from tests.gui_source import gui_source_text
import ast,json,tempfile,unittest,zipfile
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.storage import Storage
from cifereca.services import LibraryService
from cifereca.identities.directory_selection import directory_choice
from cifereca.identities.directory_migration import DirectoryMigrationError
from cifereca.catalog import LocalCatalog
from cifereca.gui.dashboard_render_controller import render_dashboard
from cifereca.gui.dashboard_page_controller import build_dashboard_page

class Core0915(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.root=self.base/'EBooks'
        self.config=Config(self.base/'config.json');self.config.set('library.directory',str(self.root));self.config.set('providers.accounts',[])
        self.config.set('identities',[{'id':'stable','label':'Hugues','directory_name':'old'}])
        self.storage=Storage(self.base/'db');self.service=LibraryService(self.config,self.storage,Diagnostics())
    def tearDown(self):self.service.close();self.tmp.cleanup()
    def book(self,folder,name='book.epub',body='first'):
        path=self.root/folder/'paris'/name;path.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('mimetype','application/epub+zip');z.writestr('META-INF/container.xml','<container/>');z.writestr('content.xhtml',body)
        return path
    def rows(self):return [{'id':'stable','label':'Hugues','directory_name':'target'}]
    def test_rescan_existing_unowned_destination_then_merge(self):
        self.book('old','source.epub');self.book('target','existing.epub');self.service.catalog_scan()
        dest=next(p for p in self.storage.placements() if '/target/' in p['path']);self.assertIsNone(dest['identity_id'])
        self.service.change_identity_directory('stable','target')
        self.assertEqual(len(self.storage.placements()),2);self.assertTrue(all(p['identity_id']=='stable' for p in self.storage.placements()))
        self.assertEqual({p.name for p in (self.root/'target/paris').iterdir()},{'source.epub','existing.epub'})
    def test_legacy_inferred_owner_does_not_block_merge(self):
        self.book('old');self.book('target','existing.epub');self.service.catalog_scan()
        with self.storage._connect() as db:db.execute("UPDATE placements SET identity_id='target' WHERE path LIKE ?",(str(self.root/'target')+'/%',))
        self.service.change_identity_directory('stable','target');self.assertTrue(all(p['identity_id']=='stable' for p in self.storage.placements()))
    def test_migration_explicitly_requalifies_unattested_source_folder_owner(self):
        self.book('old');self.service.catalog_scan()
        with self.storage._connect() as db:
            db.execute("UPDATE placements SET identity_id='old'")
            db.execute("UPDATE catalog_entries SET identity_id='old'")
        self.service.change_identity_directory('stable','target')
        self.assertTrue(all(row['identity_id']=='stable' for row in self.storage.placements()))
        self.assertTrue(all(row['identity_id']=='stable' for row in self.storage.catalog_entries()))

    def test_legacy_inference_with_acquisition_evidence_is_not_adopted(self):
        self.book('old');self.book('target','existing.epub');self.service.catalog_scan()
        with self.storage._connect() as db:db.execute("UPDATE placements SET identity_id='target' WHERE path LIKE ?",(str(self.root/'target')+'/%',))
        self.storage.acquisition_put({'provider_id':'paris','credential_id':'c','item_id':'123','identity_id':'target','status':'complete'})
        with self.assertRaises(DirectoryMigrationError):self.service.change_identity_directory('stable','target')
    def test_real_other_identity_remains_protected(self):
        self.book('old');self.book('target','existing.epub');self.service.catalog_scan()
        self.storage.identity_put({'identity_id':'target','label':'Other','directory_name':'other'})
        with self.storage._connect() as db:db.execute("UPDATE placements SET identity_id='target' WHERE path LIKE ?",(str(self.root/'target')+'/%',))
        with self.assertRaises(DirectoryMigrationError):self.service.change_identity_directory('stable','target')
    def test_rescanned_collision_retains_both_contents(self):
        self.book('old');target=self.book('target',body='other');before=target.read_bytes();self.service.catalog_scan()
        result=self.service.change_identity_directory('stable','target');self.assertEqual(result['collisions'],1)
        self.assertEqual(target.read_bytes(),before);self.assertEqual(len(list((self.root/'target/paris').glob('*.epub'))),2)
    def test_selected_existing_folder_preserves_case(self):
        path=self.root/'Hugues';path.mkdir(parents=True)
        self.assertEqual(directory_choice(str(path),self.root),'Hugues')
        self.assertEqual(directory_choice('Hugues',self.root),'Hugues')
    def test_selection_outside_root_and_root_itself_refused(self):
        for value in [str(self.base/'other'),str(self.root),'../other','x/y','..','']:
            with self.assertRaises(DirectoryMigrationError):directory_choice(value,self.root)
    def test_selection_does_not_create_directory(self):
        self.assertEqual(directory_choice('new',self.root),'new');self.assertFalse(self.root.exists())
    def test_selection_to_existing_scanned_folder_via_staged_settings(self):
        self.book('old');self.book('target','existing.epub');self.service.catalog_scan()
        data=deepcopy(self.config.data);data['identities'][0]['directory_name']=directory_choice(str(self.root/'target'),self.root)
        result=self.service.save_configuration(data);self.assertEqual(result['migrated'],1);self.assertIn('scan',result)
        self.assertFalse((self.root/'old').exists());self.assertEqual(self.config.identities()[0]['directory_name'],'target')
    def test_overview_is_local_and_has_only_connected_capabilities(self):
        session=NS(descriptor=NS(instantiation='multiple',display_name='Paris'),provider_id='paris',credential_id='c',connected=True,capabilities=NS(search=True,loans=True))
        self.service.accounts=[NS(unique_id='main',identity_id='stable',provider_id='paris',credential_id='c')];self.service._sessions={'c':session}
        self.assertEqual([r['id'] for r in self.service.dashboard_state()[0]['capabilities']],['search','loans'])
        session.connected=False;self.assertEqual(self.service.dashboard_state()[0]['capabilities'],[])
        self.service._sessions={}
    def test_overview_does_not_expose_another_identity_session(self):
        self.service._sessions={'c':NS(descriptor=NS(instantiation='multiple',display_name='Paris'),provider_id='paris',credential_id='other',connected=True,capabilities=NS(search=True))}
        self.assertEqual(self.service.dashboard_state()[0]['sources'],[]);self.service._sessions={}
    def extract(self,name):
        path=Path(__file__).resolve().parents[1]/'src/cifereca/gui/main_window.py';tree=ast.parse(gui_source_text(path))
        if name == '_UiHost':
            fn=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
        else:
            window=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MainWindow')
            matches=[n for n in window.body if isinstance(n,ast.FunctionDef) and n.name==name]
            fn=matches[0] if matches else next(n for n in ast.walk(tree)
                                             if isinstance(n,ast.FunctionDef) and n.name==name)
        future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
        return compile(ast.fix_missing_locations(ast.Module(body=[future,fn],type_ignores=[])),str(path),'exec')
    def test_public_host_snapshot_is_detached_and_selection_brokered(self):
        window=NS(service=NS(dashboard_state=Mock(return_value=[{'label':'Hugues'}])),open_identity_library=Mock(),run_opening_sync=Mock())
        ns={};exec(self.extract('_UiHost'),ns);host=ns['_UiHost'](window)
        data=host.identity_overview();data[0]['label']='Changed';self.assertEqual(window.service.dashboard_state()[0]['label'],'Hugues')
        host.open_identity_library('stable');window.open_identity_library.assert_called_once_with('stable')
        host.synchronize(deep=True);window.run_opening_sync.assert_called_once_with(None,deep=True)
    def test_public_subscription_cancel_is_idempotent(self):
        ns={};exec(self.extract('_UiHost'),ns);window=NS();host=ns['_UiHost'](window);callback=Mock()
        cancel=host.subscribe_identity_overview(callback);self.assertIn(callback,window._overview_listeners)
        cancel();cancel();self.assertEqual(window._overview_listeners,[])
    def test_name_action_selects_before_navigating(self):
        calls=[];window=NS(_activate_identity=lambda iid:calls.append(('select',iid)),navigate=lambda route:calls.append(('navigate',route)))
        ns={};exec(self.extract('open_identity_library'),ns);ns['open_identity_library'](window,'stable')
        self.assertEqual(calls,[('select','stable'),('navigate','library')])
    def test_unknown_identity_selection_rejected_before_reset(self):
        from cifereca.gui.identity_activation_controller import activate_identity
        window=NS(service=NS(identity_by_id=lambda iid:None))
        with self.assertRaises(ValueError):activate_identity(window,'unknown')
    def test_notice_builds_three_column_flow_and_retains_all_fields(self):
        def factory(**kwargs):
            widget=Mock();widget.children=[]
            widget.add.side_effect=lambda child:widget.children.append(child)
            widget.append.side_effect=lambda child:widget.children.append(child)
            widget.insert.side_effect=lambda child,pos:widget.children.append(child)
            return widget
        grid=factory();group=factory();content=factory();rows=[]
        def row():
            result=factory();rows.append(result);return result
        ns={'Gtk':NS(FlowBox=lambda:grid,SelectionMode=NS(NONE=0)),'Adw':NS(PreferencesGroup=lambda:group,ActionRow=row),'detail_display_value':lambda _label,value:(value or '—',value or '—'),'detail_subtitle_lines':lambda _section,_value:2}
        exec(self.extract('_group'),ns);ns['_group'](NS(content=content,_append_content=content.append),'Notice',[('Source','Paris'),('Format','EPUB'),('Éditeur','Long')])
        grid.set_max_children_per_line.assert_called_once_with(3);self.assertEqual(len(grid.children),3)
        self.assertEqual(group.children,[grid]);self.assertEqual(content.children,[group])
    def test_library_actions_in_header_and_advanced_menu(self):
        page=NS(header_actions=Mock(),toolbar=Mock(),connect_selected=Mock())
        controls=NS(primary=Mock(),entry=Mock())
        window=NS(
            _query_controls=Mock(return_value=controls),
            _library_search_online=Mock(),_library_selected=Mock(),
            refresh_library=Mock(),choose_acsm=Mock(),run_kobo_backup=Mock(),
            open_library_folder=Mock(),
        )
        class Widget:
            def __init__(self,*args,**kwargs):self.children=[];self.handlers={};self.kwargs=kwargs
            def append(self,child):self.children.append(child)
            def set_child(self,child):self.child=child
            def set_popover(self,child):self.popover=child
            def connect(self,name,callback):self.handlers[name]=callback
            def __getattr__(self,name):
                if name.startswith('set_'):return lambda *args:None
                raise AttributeError(name)
        class Buttons(Widget):
            @staticmethod
            def new_from_icon_name(name):return Buttons(icon_name=name)
        gtk=NS(
            Button=Buttons,MenuButton=Widget,Popover=Widget,Box=Widget,
            Orientation=NS(VERTICAL=0,HORIZONTAL=1),
        )
        ns={'MasterDetailPage':lambda *args:page,'Gtk':gtk}
        from cifereca.gui import library_page_controller
        library_page_controller.build_library_page(window, Gtk=gtk, MasterDetailPage=ns['MasterDetailPage'])
        self.assertEqual(page.header_actions.append.call_count,3)
        page.toolbar.append.assert_called_once_with(controls)
    def test_recent_refresh_in_header_not_toolbar(self):
        page=NS(header_actions=Mock(),toolbar=Mock(),connect_selected=Mock());button=Mock()
        ns={'MasterDetailPage':lambda *args:page,'Gtk':NS(Button=NS(new_from_icon_name=lambda name:button))}
        from cifereca.gui import library_page_controller
        library_page_controller.build_recent_page(
            NS(_recent_selected=Mock()), Gtk=ns['Gtk'], MasterDetailPage=ns['MasterDetailPage'],
        )
        page.header_actions.append.assert_called_once_with(button);page.toolbar.append.assert_not_called()
    def test_scan_preserves_registered_owner_in_unconfigured_directory(self):
        self.book('target');self.service.catalog_scan();self.storage.identity_put({'identity_id':'other','label':'Other','directory_name':'elsewhere'})
        with self.storage._connect() as db:db.execute("UPDATE placements SET identity_id='other' WHERE path LIKE ?",(str(self.root/'target')+'/%',))
        self.service.catalog_scan();self.assertEqual(self.storage.placements()[0]['identity_id'],'other')
    def test_chooser_cancel_keeps_entry_and_destroys_dialog(self):
        chooser=Mock();entry=Mock();window=NS(show_error=Mock());ns={'Gtk':NS(FileChooserNative=NS(new=Mock(return_value=chooser)),FileChooserAction=NS(SELECT_FOLDER=1),ResponseType=NS(ACCEPT=1)),'Gio':NS(File=NS(new_for_path=lambda path:path))}
        from cifereca.gui.settings_chooser_controller import choose_identity_directory
        choose_identity_directory(window,entry,self.root,Gtk=ns['Gtk'],Gio=ns['Gio'])
        callback=chooser.connect.call_args.args[1];callback(chooser,0)
        entry.set_text.assert_not_called();chooser.destroy.assert_called_once();self.assertIsNone(window._identity_directory_chooser)
    def test_chooser_accept_sets_existing_folder_name_without_moving_files(self):
        self.book('target');chooser=Mock();chooser.get_file.return_value.get_path.return_value=str(self.root/'target')
        entry=Mock();window=NS(show_error=Mock());ns={'Gtk':NS(FileChooserNative=NS(new=Mock(return_value=chooser)),FileChooserAction=NS(SELECT_FOLDER=1),ResponseType=NS(ACCEPT=1)),'Gio':NS(File=NS(new_for_path=lambda path:path))}
        from cifereca.gui.settings_chooser_controller import choose_identity_directory
        choose_identity_directory(window,entry,self.root,Gtk=ns['Gtk'],Gio=ns['Gio'])
        chooser.connect.call_args.args[1](chooser,1);entry.set_text.assert_called_once_with('target');window.show_error.assert_not_called()
        self.assertTrue((self.root/'target/paris/book.epub').exists());self.assertEqual(self.config.identities()[0]['directory_name'],'old')
    def test_overview_change_subscribers_called_without_fallback_widget(self):
        callback=Mock()
        render_dashboard(NS(_overview_listeners=[callback]), Gtk=None, add_css=None)
        callback.assert_called_once_with()
    def test_home_cards_use_two_column_grid_and_header_actions(self):
        boxes=[]
        def box(**kwargs):
            widget=Mock();widget.children=[];widget.append.side_effect=widget.children.append;boxes.append(widget);return widget
        grid=Mock();scroll=Mock();window=NS(action_button=Mock(return_value=Mock()))
        ns={'Gtk':NS(ScrolledWindow=lambda:scroll,PolicyType=NS(NEVER=0,AUTOMATIC=1),Box=box,Label=lambda **kw:Mock(),FlowBox=lambda:grid,Orientation=NS(VERTICAL=0,HORIZONTAL=1),SelectionMode=NS(NONE=0),Align=NS(END=1)), 'add_css':Mock(), 'AdaptiveActionBar':lambda source=None:box()}
        build_dashboard_page(window, Gtk=ns['Gtk'], add_css=ns['add_css'],
                             AdaptiveActionBar=ns['AdaptiveActionBar'])
        grid.set_max_children_per_line.assert_called_once_with(3)
        self.assertIn(boxes[2],boxes[1].children);self.assertNotIn(boxes[2],boxes[0].children)
    def test_home_name_button_uses_label_and_exact_identity(self):
        buttons=[]
        def button(**kwargs):
            widget=Mock();buttons.append((kwargs,widget));return widget
        card={'identity_id':'stable-secret-id','label':'Hugues','sources':[],'capabilities':[{'id':'loans','label':'Emprunts'}]}
        window=NS(dashboard_cards=Mock(),service=NS(dashboard_state=lambda:[card]),_sync_status_label=lambda status:'Jamais synchronisée',open_identity_library=Mock())
        window.dashboard_cards.get_first_child.return_value=None
        ns={'Gtk':NS(Box=lambda **kw:Mock(),Label=lambda **kw:Mock(),Button=button,Orientation=NS(VERTICAL=0,HORIZONTAL=1)),'add_css':Mock()}
        render_dashboard(window, Gtk=ns['Gtk'], add_css=ns['add_css'])
        self.assertEqual(buttons[0][0]['label'],'Hugues')
        buttons[0][1].connect.call_args.args[1](buttons[0][1]);window.open_identity_library.assert_called_once_with('stable-secret-id')
        window.dashboard_cards.insert.assert_called_once()
    def test_minimal_public_host_protocol_remains_compatible(self):
        from cifereca.plugin_api import UiPageHost,UiIdentityHost
        host=NS(toast=Mock(),navigate=Mock(),plugin_statuses=Mock())
        self.assertIsInstance(host,UiPageHost);self.assertNotIsInstance(host,UiIdentityHost)
    def test_actual_host_implements_optional_identity_protocol(self):
        from cifereca.plugin_api import UiIdentityHost
        ns={};exec(self.extract('_UiHost'),ns);self.assertIsInstance(ns['_UiHost'](NS()),UiIdentityHost)
    def test_settings_handler_accepts_selected_path_and_preserves_case(self):
        from . import test_settings_migration_099_dev2 as fixture
        case=fixture.SaveSettings();case.setUp()
        try:
            target=Path(case.config.get('library.directory'))/'Hugues';target.mkdir(parents=True)
            case.ns['identity_editors'][0]['directory'].text=str(target)
            self.assertTrue(case.ns['save_settings']());case.runner.submit.call_args.args[1]()
            self.assertEqual(case.service.save_configuration.call_args.args[0]['identities'][0]['directory_name'],'Hugues')
        finally:case.tearDown()
    def test_settings_handler_rejects_selected_path_outside_library(self):
        from . import test_settings_migration_099_dev2 as fixture
        case=fixture.SaveSettings();case.setUp()
        try:
            case.ns['identity_editors'][0]['directory'].text=str(self.base/'outside')
            self.assertFalse(case.ns['save_settings']());case.runner.submit.assert_not_called()
        finally:case.tearDown()
