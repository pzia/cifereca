"""Exercise package/plugin card builders with a lightweight GTK tree."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.plugin_attention import plugin_section_expanded
from cifereca.gui.store_usage_labels import store_plugin_usage_hint
from cifereca.gui.plugin_view import PLUGIN_ROLE_LABELS, plugin_role, plugin_dependents, plugin_relationships


class Widget:
    def __init__(self, **properties):
        self.children=[];self.properties=properties;self.handlers={}
    def append(self, child):self.children.append(child)
    def set_child(self, child):self.children=[child]
    def set_popover(self, child):self.popover=child
    def connect(self, signal, fn):self.handlers[signal]=fn
    def get_active(self):return self.properties.get('active',False)
    def get_expanded(self):return self.properties.get('expanded',False)
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
        gtk=NS(**{name:type(name,(Widget,),{}) for name in ['Box','Frame','Label','MenuButton','Popover','Button','SpinButton','Switch','CheckButton','Expander']},
               Orientation=NS(VERTICAL='vertical',HORIZONTAL='horizontal'),Align=NS(CENTER='center'))
        future=ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)
        adaptive=type('AdaptiveActionBar',(Widget,),{})
        def action_button(label, icon_name=None, **properties):
            return gtk.Button(label=label, icon_name=icon_name, **properties)
        ns={'Gtk':gtk,'_STATE_LABELS':{'ready':'Prêt'},'AdaptiveActionBar':adaptive,'action_button':action_button,
            'PLUGIN_ROLE_LABELS':PLUGIN_ROLE_LABELS,'plugin_role':plugin_role,
            'plugin_dependents':plugin_dependents,'plugin_relationships':plugin_relationships,
            'plugin_section_expanded':plugin_section_expanded,
            'store_plugin_usage_hint':store_plugin_usage_hint}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[future]+methods,type_ignores=[])),str(path),'exec'),ns)
        cls.builder=staticmethod(ns['_plugin_row']);cls.packager=staticmethod(ns['_append_package'])

    def setUp(self):
        self.calls=[]
        self.host=NS(
            outer=Widget(),
            _package_expansion={},
            # #908: mirror the actual PluginManagerPage.__init__ registry.
            _plugin_widgets={}, _plugin_switches={},
            _view_query='', _view_filter='all', _view_role='all',
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
        self.host._plugin_row=lambda status,package,*,statuses=():self.builder(self.host,status,package,statuses=statuses)

    def card(self):return self.builder(self.host,self.status,self.package)

    def test_plugin_row_keeps_primary_and_secondary_actions_distinct(self):
        card=self.card()
        primary=card.children[-2]
        settings=card.children[-1]
        labels=[w.properties['label'] for w in primary.children if type(w).__name__=='Button']
        self.assertEqual(labels,['Configurer…'])
        secondary=next(
            w for w in primary.children
            if type(w).__name__=='MenuButton' and w.properties.get('icon_name')=='open-menu-symbolic'
        )
        diag=secondary.popover.children[0].children[0]
        self.assertEqual(diag.properties['label'],'Diagnostic')
        diag.handlers['clicked'](None)
        self.assertEqual(self.calls,[('diag','plugin.test')])
        self.assertEqual(settings.children[0].children[0].properties['label'],'Priorité')
        self.assertEqual(settings.children[1].children[0].properties['label'],'Activé')
        self.assertEqual(type(settings.children[1].children[-1]).__name__,'Switch')

    def test_public_identity_controls_are_in_primary_actions(self):
        self.status.descriptor.instantiation='singleton_public'
        primary=self.card().children[-2]
        menu=next(
            w for w in primary.children
            if type(w).__name__=='MenuButton' and w.properties.get('label')=='Identités…'
        )
        self.assertEqual(menu.popover.children[0].children[0].properties['label'],'Hugues')

    def test_package_is_one_card_with_one_hamburger_for_package_actions(self):
        self.packager(self.host,self.package,[self.status,self.status])
        self.assertEqual(len(self.host.outer.children),1)
        package_box=self.host.outer.children[0].children[0]
        header=package_box.children[0]
        package_menu=next(
            w for w in header.children
            if type(w).__name__=='MenuButton' and w.properties.get('icon_name')=='open-menu-symbolic'
        )
        labels=[
            w.properties['label']
            for w in package_menu.popover.children[0].children
            if type(w).__name__=='Button'
        ]
        self.assertEqual(labels,['Revenir à 1.1.0','Désinstaller le package'])
        expanders=[child for child in package_box.children if type(child).__name__=='Expander']
        self.assertEqual(len(expanders),1)
        plugin_frames=expanders[0].children[0].children
        self.assertEqual(len(plugin_frames),2)
        self.assertEqual(expanders[0].properties['expanded'],False)

    def test_package_remove_names_scope_and_affected_plugins(self):
        self.packager(self.host,self.package,[self.status])
        package_box=self.host.outer.children[0].children[0]
        package_menu=next(
            w for w in package_box.children[0].children
            if type(w).__name__=='MenuButton' and w.properties.get('icon_name')=='open-menu-symbolic'
        )
        remove=next(
            w for w in package_menu.popover.children[0].children
            if w.properties.get('label')=='Désinstaller le package'
        )
        remove.handlers['clicked'](None)
        call=self.calls[-1]
        self.assertEqual(call[0],'remove')
        self.assertEqual(call[1][0],'package.test')
        self.assertEqual(call[1][1],'Package Test')
        self.assertEqual(call[1][2],('Test',))

