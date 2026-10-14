from tests.gui_source import gui_source_text
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from cifereca.gui.shell_model import menu_sections, validate_action_selection
from cifereca.gui import header_action_controller
from cifereca.gui.shortcuts import SHORTCUTS, adjacent_route, shortcuts_text
from cifereca.plugin_api import UiPageContribution, UiActionContribution

class Action:
    def __init__(self,name):self.name=name;self.callback=None
    @classmethod
    def new(cls,name,parameter):return cls(name)
    def connect(self,signal,callback):self.callback=callback
    def activate(self):self.callback(self,None)
class ActionGroup:
    def __init__(self):self.actions={}
    def add_action(self,action):self.actions[action.name]=action
class Menu:
    def __init__(self):self.entries=[];self.sections=[]
    def append(self,label,action):self.entries.append((label,action))
    def append_section(self,label,section):self.sections.append((label,section))
class Widget:
    def __init__(self,**kwargs):self.children=[];self.properties=kwargs
    def append(self,child):self.children.append(child)
    def pack_start(self,child):self.children.append(child)
    def pack_end(self,child):self.end=child
    def set_title_widget(self,child):self.title=child
    def set_popover(self,child):self.popover=child
    def set_menu_model(self,model):self.model=model
    def popdown(self):self.closed=True
    def popup(self):self.opened=True
    def connect(self,*args):pass
    def __getattr__(self,name):
        if name=='add_css_class':return lambda *args:None
        if name.startswith('set_'):return lambda value:self.properties.__setitem__(name[4:],value)
        raise AttributeError(name)
    @classmethod
    def new_from_model(cls,model):obj=cls();obj.model=model;return obj

class ShellControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=Path(__file__).resolve().parents[1]/'src/cifereca/gui/main_window.py'
        cls.path=source
        tree=ast.parse(gui_source_text(source));main=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MainWindow')
        names={'_create_shell_header','_ensure_pages_menu_rightmost','_refresh_header_actions','_invoke_ui_action','_install_shell_shortcuts','_quit_application','_open_pages_menu','_cycle_page'}
        methods=[n for n in main.body if isinstance(n,ast.FunctionDef) and n.name in names]
        future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
        gtk=NS(Button=Widget,HeaderBar=Widget,Label=Widget,Box=Widget,DropDown=Widget,MenuButton=Widget,PopoverMenu=Widget,SignalListItemFactory=Widget,Orientation=NS(VERTICAL=0,HORIZONTAL=1))
        ns={'Gtk':gtk,'Adw':gtk,'Gio':NS(Menu=Menu,SimpleAction=Action,SimpleActionGroup=ActionGroup),
            'menu_sections':menu_sections,'validate_action_selection':validate_action_selection,'visible_actions':lambda *args:[],'clear_box':lambda box:box.children.clear(),'header_action_controller':header_action_controller}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[future]+methods,type_ignores=[])),str(source),'exec'),ns)
        cls.methods=ns
    def call(self,name,host,*args):return self.methods[name](host,*args)
    def pages(self):
        return [UiPageContribution('settings','Paramètres',factory=lambda h:None,group='configuration'),
                UiPageContribution('search','Recherche',factory=lambda h:None),
                UiPageContribution('library','Bibliothèque',factory=lambda h:None,actions=(
                    UiActionContribution('import','Importer',lambda host:host.calls.append('import'),global_action=True),
                    UiActionContribution('local','Local',lambda host:None)))]
    def test_menu_order_and_context_actions(self):
        result=menu_sections(self.pages(),'search')
        self.assertEqual([x[0] for x in result],['Pages','Configuration / Technique','Actions'])
        self.assertEqual([label for label,_ in result[-1][1]],['Importer'])
        self.assertEqual([label for label,_ in menu_sections(self.pages(),'library')[-1][1]],['Importer','Local'])
    def test_navigation_cycles_and_empty_input(self):
        self.assertEqual(adjacent_route(['a','b'],'b',1),'a')
        self.assertEqual(adjacent_route(['a','b'],'a',-1),'b')
        self.assertEqual(adjacent_route(['a','b'],'missing',1),'a')
        self.assertIsNone(adjacent_route([],'a',1))
    def test_shortcut_keys_do_not_conflict(self):
        keys=[key for _,values,_ in SHORTCUTS for key in values]
        self.assertEqual(len(keys),len(set(keys)))
        self.assertIn('Échap',shortcuts_text());self.assertIn('Ctrl+Q',shortcuts_text())
    def host(self):
        host=NS(plugin_manager=None,diagnostics=NS(add=lambda *args,**kwargs:None),_account_changed=lambda *args:None,_identity_changed=lambda *args:None)
        host._UiHost=lambda window:window
        host._ensure_pages_menu_rightmost=lambda:self.call('_ensure_pages_menu_rightmost',host)
        return host
    def test_menu_is_native_and_rightmost_application_control(self):
        host=self.host();header=self.call('_create_shell_header',host)
        self.assertEqual(header.end.children,[host.identity_dropdown,host.account_dropdown,host.statistics_button,host.pages_menu_button])
        self.assertIs(host.pages_menu_button.popover,host.shell_menu)
        self.assertIsInstance(host.shell_menu.model,Menu)
    def test_model_actions_keep_correct_callback_and_refresh_replaces_group(self):
        host=self.host();self.call('_create_shell_header',host)
        host.calls=[];host.navigate=lambda route:host.calls.append(route)
        host._pages={p.route:p for p in self.pages()};host.show_error=lambda *args:None
        host._invoke_ui_action=lambda action,h:self.call('_invoke_ui_action',host,action,h)
        host.insert_action_group=lambda prefix,group:setattr(host,'group',group)
        self.call('_refresh_header_actions',host,'search')
        model=host.shell_menu.model
        for label,section in model.sections:
            for caption,detail in section.entries:
                if caption in {'Recherche','Bibliothèque','Importer'}:
                    host.group.actions[detail.split('.')[1]].activate()
        self.assertEqual(host.calls,['library','search','import'])
        previous=host.group;self.call('_refresh_header_actions',host,'library')
        self.assertIsNot(host.group,previous)
        self.assertTrue(any(caption=='Local' for _,section in host.shell_menu.model.sections for caption,_ in section.entries))
    def test_registration_dispatches_each_shortcut_to_its_own_action(self):
        calls=[];actions={};bindings={};app=NS(set_accels_for_action=lambda name,keys:bindings.__setitem__(name,keys))
        host=NS(get_application=lambda:app,add_action=lambda action:actions.__setitem__(action.name,action),
            _quit_application=lambda:calls.append('quit'),close=lambda:calls.append('close'),
            _open_pages_menu=lambda:calls.append('menu'),_focus_search=lambda:calls.append('search'),
            _show_settings=lambda:calls.append('settings'),_cycle_page=lambda value:calls.append(value),
            _show_shortcuts=lambda:calls.append('help'),reconnect_sources=lambda:calls.append('reconnect'))
        # Relative shortcut import in the real method resolves in its original module.
        self.methods['__package__']='cifereca.gui'
        self.call('_install_shell_shortcuts',host)
        self.assertEqual(bindings['win.pages-menu'],['F10','<Primary>m'])
        actions['quit'].activate();actions['pages-menu'].activate();actions['previous-page'].activate()
        self.assertEqual(calls,['quit','menu',-1])
    def test_quit_closes_before_shutdown(self):
        events=[];application=NS(quit=lambda:events.append('quit'))
        host=NS(close=lambda:events.append('close'),get_application=lambda:application if not events else None)
        self.call('_quit_application',host);self.assertEqual(events,['close','quit'])
    def test_startup_menu_shortcut_does_not_raise(self):
        self.call('_open_pages_menu',NS())
        host=NS(pages_menu_button=Widget());self.call('_open_pages_menu',host)
        self.assertTrue(host.pages_menu_button.opened)
