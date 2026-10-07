import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

FIELDS = ('all', 'title', 'author', 'isbn', 'subject', 'series', 'collection', 'language')

@dataclass(frozen=True)
class QueryState:
    text: str = ''
    field: str = 'all'
    scope: str = 'all'
    providers: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()

class Widget:
    def __init__(self, *args, **kwargs):
        self.children=[]; self.signals={}; self.text=''; self.selected=0; self.active=False; self.label=kwargs.get('label','')
    def connect(self, name, callback): self.signals.setdefault(name, []).append(callback)
    def emit(self, name):
        for callback in list(self.signals.get(name, ())): callback(self)
    def append(self, child): self.children.append(child)
    def insert(self, child, *_): self.children.append(child)
    def get_parent(self): return None
    def set_child(self, child): self.child=child
    def set_popover(self, popover): self.popover=popover
    def set_label(self, label): self.label=label
    def set_tooltip_text(self, text): self.tooltip=text
    def add_css_class(self, _name): pass
    def set_text(self, text):
        if self.text == text: return
        self.text=text; self.emit('search-changed')
    def get_text(self): return self.text
    def set_selected(self, selected):
        if self.selected == selected: return
        self.selected=selected; self.emit('notify::selected')
    def get_selected(self): return self.selected
    def set_active(self, active):
        if self.active == active: return
        self.active=active; self.emit('toggled')
    def get_active(self): return self.active
    @classmethod
    def new_from_strings(cls, *_): return cls()
    def __getattr__(self, name):
        if name.startswith('set_'): return lambda *args: None
        raise AttributeError(name)

Gtk=NS(MenuButton=Widget,Popover=Widget,Box=Widget,Button=Widget,CheckButton=Widget,Label=Widget,
       SearchEntry=Widget,FlowBox=Widget,DropDown=Widget,FlowBoxChild=Widget,Align=NS(START=0),
       Orientation=NS(VERTICAL=1),SelectionMode=NS(NONE=0))

source=Path(__file__).resolve().parents[1]/'src/cifereca/gui/query_controls.py'
tree=ast.parse(source.read_text())
classes=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name in {'MultiSelect','QueryControls'}]
ns={'Gtk':Gtk,'QueryState':QueryState,'FIELDS':FIELDS,'provider_search_summary':lambda *_:''}
exec(compile(ast.fix_missing_locations(ast.Module(body=classes,type_ignores=[])),str(source),'exec'),ns)
MultiSelect=ns['MultiSelect']; QueryControls=ns['QueryControls']

class QueryControlBatching(unittest.TestCase):
    def test_multiselect_programmatic_changes_emit_once(self):
        calls=[]
        control=MultiSelect('Formats',[('epub','EPUB'),('pdf','PDF'),('mp3','MP3')],lambda:calls.append(control.values()))
        control.set_values(('epub','pdf','mp3'))
        self.assertEqual(calls,[('epub','pdf','mp3')])
        control.set_values(())
        self.assertEqual(calls,[('epub','pdf','mp3'),()])
        control.set_values(())
        self.assertEqual(len(calls),2)
        control.buttons['epub'].set_active(True)
        self.assertEqual(calls[-1],('epub',))
        self.assertEqual(len(calls),3)

    def test_set_state_is_atomic_and_duplicate_signal_is_ignored(self):
        calls=[]
        control=QueryControls([('paris','Paris'),('gallica','Gallica')],lambda:calls.append(control.state()),lambda:None)
        control._provider_search_capabilities={}
        target=QueryState('Dumas','author','physical',('paris','gallica'),('epub','pdf'),('fr','en'))
        control.set_state(target)
        self.assertEqual(calls,[target])
        control.entry.emit('search-changed')
        self.assertEqual(calls,[target])
        control.set_state(target)
        self.assertEqual(calls,[target])
        control.types.buttons['mp3'].set_active(True)
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[-1].types,('epub','pdf','mp3'))

if __name__ == '__main__': unittest.main()

