"""Exercise package/plugin card builders with a lightweight GTK tree."""
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
        gtk=NS(**{name:type(name,(Widget,),{}) for name in ['Box','Frame','Label','MenuButton','Popover','Button','SpinButton','Switch','CheckButton']},
               Orientation=NS(VERTICAL='vertical',HORIZONTAL='horizontal'),Align=NS(CENTER='center'))
        future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
        adaptive=type('AdaptiveActionBar',(Widget,),{})
        def action_button(label, icon_name=None, **properties):
            return gtk.Button(label=label, icon_name=icon_name, **properties)
        ns={'Gtk':gtk,'_STATE_LABELS':{'ready':'Prêt'},'AdaptiveActionBar':adaptive,'action_button':action_button}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[future]+methods,type_ignores=[])),str(path),'exec'),ns)
        cls.builder=staticmethod(ns['_plugin_row']);cls.packager=staticmethod(ns['_append_package'])

    def setUp(self):
        self.calls=[]
        self.host=NS(
            outer=Widget(),
            window=NS(service=NS(identities=[NS(label='Hugues',identity_id='hugues')])),
            manager=NS(enabled_for_identity=lambda *args:True),
            _configure=lambda pid:self.calls.append(('config',pid)),
            _diagnostic=lambda pid:self.calls.append(('diag',pid)),
            _rollback=lambda pid:self.calls.append(('rollback',pid)),
            _confirm_remove=lambda *args:self.calls.append(('remove',args)),
            _priority=lambda *args:None,
            _toggle=lambda *args:self.calls.append(('toggle',args)),
            _identity_toggle=lambda *args:None,
        )
        d=NS(id='plugin.test',display_name='Test',package_id='package.test',version='1.2.0',type='provider',
             description='Description compacte',help_text='Aide détaillée',config_schema={'x':1},instantiation='multiple')
        self.status=NS(descriptor=d,state='ready',reason='',enabled=True,effective_priority=100)
        self.package={
            'package_id':'package.test','name':'Package Test','publisher':'cifereca',
            'active':'1.2.0','expected_version':'1.2.0','freshness':'up_to_date',
            'freshness_reason':'','previous':'1.1.0','builtin':False,
        }
        self.host._plugin_row=lambda status,package:self.builder(self.host,status,package)

    def card(self):return self.builder(self.host,self.status,self.package)

    def test_plugin_row_keeps_only_plugin_scoped_actions(self):
        card=self.card();header=card.children[0];footer=card.children[-1]
        self.assertEqual(header.children[0].properties['label'],'Test')
        self.assertEqual(type(header.children[-1]).__name__,'MenuButton')
        labels=[w.properties['label'] for w in footer.children if type(w).__name__=='Button']
        self.assertEqual(labels,['Configurer…','Diagnostic'])
        self.assertNotIn('Désinstaller',labels)
        self.assertFalse(any(str(label).startswith('Revenir à') for label in labels))
        self.assertEqual(type(footer.children[-1]).__name__,'Switch')

    def test_public_identity_controls_are_in_plugin_footer(self):
        self.status.descriptor.instantiation='singleton_public'
        footer=self.card().children[-1]
        menu=next(w for w in footer.children if type(w).__name__=='MenuButton')
        self.assertEqual(menu.properties['label'],'Identités…')
        self.assertEqual(menu.popover.children[0].children[0].properties['label'],'Hugues')

    def test_package_is_one_card_with_one_set_of_package_actions(self):
        self.packager(self.host,self.package,[self.status,self.status])
        self.assertEqual(len(self.host.outer.children),1)
        frame=self.host.outer.children[0]
        package_box=frame.children[0]
        bars=[child for child in package_box.children if type(child).__name__=='AdaptiveActionBar']
        self.assertEqual(len(bars),1)
        labels=[w.properties['label'] for w in bars[0].children if type(w).__name__=='Button']
        self.assertEqual(labels,['Revenir à 1.1.0','Désinstaller le package'])
        plugin_frames=[child for child in package_box.children if type(child).__name__=='Frame']
        self.assertEqual(len(plugin_frames),2)

    def test_package_remove_names_scope_and_affected_plugins(self):
        self.packager(self.host,self.package,[self.status])
        package_box=self.host.outer.children[0].children[0]
        bar=next(child for child in package_box.children if type(child).__name__=='AdaptiveActionBar')
        remove=next(w for w in bar.children if w.properties.get('label')=='Désinstaller le package')
        remove.handlers['clicked'](None)
        call=self.calls[-1]
        self.assertEqual(call[0],'remove')
        self.assertEqual(call[1][0],'package.test')
        self.assertEqual(call[1][1],'Package Test')
        self.assertEqual(call[1][2],('Test',))
