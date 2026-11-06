import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

from cifereca.core.document_filter import loaded_document_facets

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
    def remove(self, child): self.children.remove(child)
    def insert(self, child, *_): self.children.append(child)
    def get_parent(self): return None
    def set_child(self, child): self.child=child
    def set_popover(self, popover): self.popover=popover
    def set_visible(self, visible): self.visible=visible
    def get_model(self): return self.model
    def set_model(self, model): self.model=model
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
    def new_from_strings(cls, labels):
        widget = cls()
        widget.model = NS(get_string=labels.__getitem__)
        return widget
    def __getattr__(self, name):
        if name.startswith('set_'): return lambda *args: None
        raise AttributeError(name)

Gtk=NS(MenuButton=Widget,Popover=Widget,Box=Widget,Button=Widget,CheckButton=Widget,Label=Widget,
       SearchEntry=Widget,ScrolledWindow=Widget,DropDown=Widget,StringList=NS(new=list),Align=NS(START=0),
       Orientation=NS(VERTICAL=1,HORIZONTAL=2),PolicyType=NS(AUTOMATIC=0,NEVER=1))

source=Path(__file__).resolve().parents[1]/'src/cifereca/gui/query_controls.py'
tree=ast.parse(source.read_text())
classes=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name in {'MultiSelect','QueryControls','LocalQueryControls'}]
ns={'Gtk':Gtk,'QueryState':QueryState,'FIELDS':FIELDS,'provider_search_summary':lambda *_:'',
    'loaded_document_facets': loaded_document_facets}
exec(compile(ast.fix_missing_locations(ast.Module(body=classes,type_ignores=[])),str(source),'exec'),ns)
MultiSelect=ns['MultiSelect']; QueryControls=ns['QueryControls']
LocalQueryControls = ns['LocalQueryControls']

class QueryControlBatching(unittest.TestCase):
    def test_local_document_choices_are_silent_and_keep_only_valid_selections(self):
        calls = []
        control = LocalQueryControls(lambda: calls.append(control.state()), 'Filtre')
        pdf = {'format': 'application/pdf'}
        physical = {'medium': 'physical'}
        unknown = {}
        self.assertFalse(control.types.visible)
        self.assertFalse(control.scope.visible)
        control.set_document_choices((pdf, physical, unknown))
        self.assertTrue(control.types.visible)
        self.assertTrue(control.scope.visible)
        self.assertEqual(tuple(control.types.buttons), ('pdf',))
        self.assertEqual(calls, [])

        control.types.set_values(('pdf',))
        self.assertTrue(control.matches_document(pdf))
        self.assertFalse(control.matches_document(physical))
        control.scope.set_selected(1)
        self.assertFalse(control.matches_document(pdf))
        control.set_document_choices((pdf, unknown))
        self.assertEqual(control.state().scope, 'all')
        self.assertEqual(control.state().types, ('pdf',))
        self.assertEqual(len(calls), 2)
        control.set_document_choices((unknown,))
        self.assertFalse(control.types.visible)
        self.assertFalse(control.scope.visible)
        self.assertTrue(control.matches_document(unknown))
        self.assertEqual(len(calls), 2)

    def test_local_choices_keep_surviving_filters_without_notifying(self):
        calls = []
        control = LocalQueryControls(
            lambda: calls.append((
                control.filter_text(), control.selected_sources(),
                control.selected_languages(),
            )),
            'Titre ou auteur',
        )
        self.assertIs(control.widget, control)
        control.set_choices(
            sources=(('a', 'Catalogue A'), ('b', 'Catalogue B')),
            languages=(('fr', 'Français'), ('en', 'English')),
        )
        self.assertEqual(calls, [])
        control.provider.set_values(('a', 'b'))
        control.languages.set_values(('fr', 'en'))
        control.entry.set_text('  Dumas  ')
        self.assertEqual(calls[-1], ('Dumas', ('a', 'b'), ('fr', 'en')))
        self.assertEqual(len(calls), 3)

        control.set_choices(sources=(('b', 'Catalogue B'),))
        control.entry.emit('search-changed')
        control.entry.emit('activate')
        self.assertEqual(len(calls), 3)
        self.assertEqual(control.selected_sources(), ('b',))
        self.assertEqual(control.selected_languages(), ('fr', 'en'))
        control.provider.set_values(())
        self.assertEqual(calls[-1], ('Dumas', (), ('fr', 'en')))

    def test_multiselect_programmatic_changes_emit_once(self):
        calls=[]
        control=MultiSelect('Format',[('epub','EPUB'),('pdf','PDF'),('mp3','MP3')],lambda:calls.append(control.values()),'Choisir un format')
        self.assertEqual(control.label,'Format')
        self.assertEqual(control.tooltip,'Choisir un format')
        control.set_values(('epub','pdf','mp3'))
        self.assertEqual(control.label,'Format : EPUB +2')
        self.assertEqual(calls,[('epub','pdf','mp3')])
        control.set_values(())
        self.assertEqual(control.label,'Format')
        self.assertEqual(control.tooltip,'Choisir un format')
        self.assertEqual(calls,[('epub','pdf','mp3'),()])
        control.set_values(())
        self.assertEqual(len(calls),2)
        control.buttons['epub'].set_active(True)
        self.assertEqual(control.label,'Format : EPUB')
        self.assertEqual(calls[-1],('epub',))
        self.assertEqual(len(calls),3)

    def test_source_choice_refresh_keeps_selection_and_drops_disabled_provider(self):
        calls = []
        control = QueryControls(
            [('baen', 'Baen'), ('ebooksgratuits', 'Ebooks libres et gratuits')],
            lambda: calls.append(control.state()), lambda: None,
        )
        control.provider.set_values(('ebooksgratuits',))
        before = control.state()
        calls.clear()
        control.set_provider_choices([
            ('ebooksgratuits', 'Ebooks libres et gratuits'),
            ('gutenberg', 'Gutenberg'),
        ])
        self.assertEqual(control.state(), before)
        self.assertNotIn('baen', control.provider.buttons)
        self.assertIn('gutenberg', control.provider.buttons)
        self.assertEqual(calls, [])
        control.provider.set_values(('gutenberg',))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[-1].providers, ('gutenberg',))
        control.set_provider_choices([('ebooksgratuits', 'Ebooks libres et gratuits')])
        self.assertEqual(control.state().providers, ())
        self.assertEqual(len(calls), 2)

    def test_set_state_is_atomic_and_duplicate_signal_is_ignored(self):
        calls=[]
        control=QueryControls([('paris','Paris'),('gallica','Gallica')],lambda:calls.append(control.state()),lambda:None)
        self.assertEqual(control._provider_search_capabilities,{})
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

    def test_local_document_filter_labels_reflect_selected_attested_choices(self):
        calls = []
        control = LocalQueryControls(lambda: calls.append(True), 'Filtre')
        self.assertEqual(control.active_document_labels(), ())
        control.set_document_choices((
            {'medium': 'digital', 'format': 'application/epub+zip'},
            {'medium': 'physical'},
        ))
        self.assertEqual(control.active_document_labels(), ())
        control.types.set_values(('epub',))
        self.assertEqual(control.active_document_labels(), ('EPUB',))
        control.scope.set_selected(1)
        self.assertEqual(control.active_document_labels(), ('Type · Physique', 'EPUB'))
        control.types.set_values(())
        self.assertEqual(control.active_document_labels(), ('Type · Physique',))
        # Replacing observed choices silently resets an obsolete scope.
        control.set_document_choices(({'medium': 'unknown'},))
        self.assertEqual(control.active_document_labels(), ())

if __name__ == '__main__': unittest.main()
