"""Exercise real card builders with a lightweight GTK tree, without a display."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

class Widget:
    def __init__(self, **properties):
        self.children=[];self.properties=properties;self.handlers={}
    def append(self, child):self.children.append(child)
    def set_child(self, child):self.children=[child]
    def set_popover(self, child):self.popover=child
    def connect(self, signal, fn):self.handlers[signal]=fn
    def get_active(self):return self.properties.get('active',False)
    @classmethod
    def new_with_range(cls,*args):return cls()
    def __getattr__(self,name):
        if name.startswith('set_'):
            return lambda value:self.properties.__setitem__(name[4:],value)
        if name=='add_css_class':return lambda value:None
        raise AttributeError(name)

class CardLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path(__file__).resolve().parents[1]/'src/cifereca/gui/plugin_manager_page.py'
        tree=ast.parse(path.read_text())
        obj=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='PluginManagerPage')
        methods=[n for n in obj.body if isinstance(n,ast.FunctionDef) and n.name in {'_append_package','_plugin_row'}]
        gtk=NS(**{name:type(name,(Widget,),{}) for name in ['Box','Frame','Label','MenuButton','Popover','Button','SpinButton','Switch','CheckButton']},Orientation=NS(VERTICAL='vertical',HORIZONTAL='horizontal'),Align=NS(CENTER='center'))
        future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
        ns={'Gtk':gtk,'_STATE_LABELS':{'ready':'Prêt'}}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[future]+methods,type_ignores=[])),str(path),'exec'),ns)
        cls.builder=staticmethod(ns['_plugin_row']);cls.packager=staticmethod(ns['_append_package'])
    def setUp(self):
        self.calls=[]
        self.host=NS(outer=Widget(),window=NS(service=NS(identities=[NS(label='Hugues',identity_id='hugues')])),manager=NS(enabled_for_identity=lambda *args:True),
            _configure=lambda pid:self.calls.append(('config',pid)),_diagnostic=lambda pid:self.calls.append(('diag',pid)),_rollback=lambda pid:self.calls.append(('rollback',pid)),_confirm_remove=lambda pid:self.calls.append(('remove',pid)),_priority=lambda *args:None,_toggle=lambda *args:self.calls.append(('toggle',args)),_identity_toggle=lambda *args:None)
        d=NS(id='plugin.test',display_name='Test',package_id='package.test',version='1.2.0',type='provider',description='Description compacte',help_text='Aide détaillée',config_schema={'x':1},instantiation='multiple')
        self.status=NS(descriptor=d,state='ready',reason='',enabled=True,effective_priority=100)
        self.package={'package_id':'package.test','publisher':'cifereca','previous':'1.1.0','builtin':False}
        self.host._plugin_row=lambda status,package:self.builder(self.host,status,package)
    def card(self):return self.builder(self.host,self.status,self.package)
    def test_version_and_help_are_last_header_items(self):
        card=self.card();header=card.children[0]
        self.assertEqual(header.children[-2].properties['label'],'v1.2.0')
        help_button=header.children[-1]
        self.assertEqual(type(help_button).__name__,'MenuButton')
        self.assertEqual(help_button.popover.children[0].properties['label'],'Aide détaillée')
        self.assertEqual(len(card.children),3)
    def test_all_action_buttons_share_footer_and_switch_is_last(self):
        card=self.card();footer=card.children[-1]
        self.assertEqual([w.properties['label'] for w in footer.children if type(w).__name__=='Button'],['Configurer…','Diagnostic','Revenir à 1.1.0','Désinstaller'])
        self.assertEqual(type(footer.children[-1]).__name__,'Switch')
        self.assertTrue(footer.children[-2].properties['hexpand'])
        footer.children[1].handlers['clicked'](None)
        footer.children[3].handlers['clicked'](None)
        self.assertEqual(self.calls,[('diag','plugin.test'),('remove','package.test')])
    def test_public_identity_controls_are_in_footer_popover(self):
        self.status.descriptor.instantiation='singleton_public'
        card=self.card();footer=card.children[-1]
        menu=next(w for w in footer.children if type(w).__name__=='MenuButton')
        self.assertEqual(menu.properties['label'],'Identités…')
        self.assertEqual(menu.popover.children[0].children[0].properties['label'],'Hugues')
    def test_each_plugin_has_one_card_and_one_footer(self):
        self.packager(self.host,self.package,[self.status,self.status])
        self.assertEqual(len(self.host.outer.children),2)
        for frame in self.host.outer.children:
            self.assertEqual(type(frame.children[0].children[-1].children[-1]).__name__,'Switch')
