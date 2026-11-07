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
        self.children=[]; self.signals={}; self.text=''; self.selected=0; self.active=False; self.label=kwargs.get('label',''); self.sensitive=True
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
    def set_sensitive(self, sensitive): self.sensitive=sensitive
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

    def test_3687_provisional_document_facet_keeps_only_attested_selected_choices(self):
        calls = []
        control = LocalQueryControls(lambda: calls.append(control.state()), 'Filtre')
        full = (
            {'medium': 'physical'},
            {'medium': 'digital', 'format': 'application/pdf'},
            {'medium': 'digital', 'format': 'application/epub+zip'},
        )
        control.set_document_choices(full)
        self.assertIn('physical', control._attested_document_scopes)
        self.assertEqual(control._attested_document_formats, frozenset({'pdf', 'epub'}))
        control.scope.set_selected(control._scope_values.index('physical'))
        control.types.set_values(('pdf',))
        before = len(calls)
        self.assertEqual(control.state().scope, 'physical')
        self.assertEqual(control.state().types, ('pdf',))

        # A provisional top-N containing only EPUB/digital cannot silently
        # turn active Physical/PDF into All and expose other rows/actions.
        control.set_document_choices(
            ({'medium': 'digital', 'format': 'application/epub+zip'},),
            provisional=True,
        )
        self.assertEqual(control.state().scope, 'physical')
        self.assertEqual(control.state().types, ('pdf',))
        self.assertTrue(control.scope.visible)
        self.assertTrue(control.types.visible)
        self.assertEqual(len(calls), before)
        self.assertFalse(control.matches_document(
            {'medium': 'digital', 'format': 'application/epub+zip'},
        ))
        self.assertEqual(control.active_document_labels(), ('Type · Physique', 'PDF'))

        # The final, exhaustive result removes absent choices with no
        # synthetic user event; this reset must not become a remote query.
        control.set_document_choices(({'medium': 'digital', 'format': 'application/epub+zip'},))
        self.assertEqual(control.state().scope, 'all')
        self.assertEqual(control.state().types, ())
        self.assertEqual(tuple(control.types.buttons), ('epub',))
        self.assertEqual(len(calls), before)

    def test_3687_unknown_support_selection_survives_provisional_head(self):
        control = LocalQueryControls(lambda: None, 'Filtre')
        control.set_document_choices((
            {'medium': 'unknown'},
            {'medium': 'digital', 'format': 'application/pdf'},
        ))
        self.assertIn('unknown', control._attested_document_scopes)
        control.scope.set_selected(control._scope_values.index('unknown'))
        control.set_document_choices(
            ({'medium': 'digital', 'format': 'application/pdf'},),
            provisional=True,
        )
        self.assertEqual(control.state().scope, 'unknown')
        self.assertTrue(control.scope.visible)
        self.assertFalse(control.matches_document(
            {'medium': 'digital', 'format': 'application/pdf'},
        ))
        control.set_document_choices(({'medium': 'digital', 'format': 'application/pdf'},))
        self.assertEqual(control.state().scope, 'all')

    def test_3687_provisional_document_attestation_clears_on_identity_reset(self):
        control = LocalQueryControls(lambda: None, 'Filtre')
        control.set_document_choices((
            {'medium': 'physical'},
            {'medium': 'digital', 'format': 'application/pdf'},
        ))
        control.scope.set_selected(control._scope_values.index('physical'))
        control.types.set_values(('pdf',))

        control.set_document_choices((), provisional=True)
        self.assertEqual(control.state().scope, 'physical')
        self.assertEqual(control.state().types, ('pdf',))
        control.set_document_choices(())  # completed error / A -> B switch
        self.assertEqual(control._attested_document_scopes, frozenset())
        self.assertEqual(control._attested_document_formats, frozenset())
        self.assertEqual(control.state().scope, 'all')
        self.assertEqual(control.state().types, ())
        control.set_document_choices(
            ({'medium': 'digital', 'format': 'application/epub+zip'},),
            provisional=True,
        )
        self.assertEqual(control.state().scope, 'all')
        self.assertNotIn('pdf', control.types.buttons)
        self.assertNotIn('physical', control._scope_values)

    def test_3687_recommendations_marks_partial_not_final_as_provisional(self):
        source = (Path(__file__).resolve().parents[1] /
                  'src/plugins/recommendations/recommendations.py').read_text()
        tree = ast.parse(source)
        view_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == 'UxRecommendationsView'
        )
        flush = next(
            node for node in view_class.body
            if isinstance(node, ast.FunctionDef) and node.name == '_flush_partial'
        )
        calls = [
            (node.func.attr, node.lineno)
            for node in ast.walk(flush)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ('_set_provisional', '_reload_document_choices')
        ]
        self.assertEqual([name for name, _line in sorted(calls, key=lambda row: row[1])],
                         ['_set_provisional', '_reload_document_choices'])
        self.assertIn('provisional=bool(getattr(self, "_provisional_active", False))',
                      source)
        self.assertIn('def _reload_document_choices(self) -> None:', source)

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

    def test_3687_shared_reset_restores_search_initial_scope_in_one_change(self):
        calls = []
        control = QueryControls(
            [('paris', 'Paris')], lambda: calls.append(control.state()),
            lambda: None,
        )
        initial = control.state()
        self.assertEqual(initial.scope, 'digital')
        self.assertFalse(control.clear_button.sensitive)
        control.entry.set_text('Dumas')
        control.field.set_selected(2)
        control.scope.set_selected(1)
        control.provider.set_values(('paris',))
        control.languages.set_values(('fr',))
        control.types.set_values(('epub',))
        self.assertTrue(control.clear_button.sensitive)
        calls.clear()
        control.clear_button.emit('clicked')
        self.assertEqual(control.state(), initial)
        self.assertEqual(calls, [initial])
        self.assertFalse(control.clear_button.sensitive)
        control.clear_button.emit('clicked')
        self.assertEqual(calls, [initial])

    def test_3687_local_reset_clears_view_filters_not_available_facets(self):
        calls = []
        control = LocalQueryControls(
            lambda: calls.append(control.state()), 'Filtrer les recommandations',
        )
        control.enable_field_filter()
        control.set_choices(
            sources=(('paris', 'Paris'), ('kobo', 'Kobo')),
            languages=(('fr', 'Français'), ('en', 'English')),
        )
        control.set_document_choices((
            {'medium': 'physical'},
            {'medium': 'digital', 'format': 'application/pdf'},
        ))
        baseline = control.state()
        self.assertEqual(baseline, QueryState())
        control.entry.set_text('autrice')
        control.field.set_selected(2)
        control.provider.set_values(('paris',))
        control.languages.set_values(('fr',))
        control.scope.set_selected(control._scope_values.index('physical'))
        control.types.set_values(('pdf',))
        self.assertTrue(control.clear_button.sensitive)
        calls.clear()
        control.reset_filters()
        self.assertEqual(control.state(), baseline)
        self.assertEqual(calls, [baseline])
        self.assertFalse(control.clear_button.sensitive)
        self.assertEqual(tuple(control.provider.buttons), ('paris', 'kobo'))
        self.assertEqual(tuple(control.languages.buttons), ('fr', 'en'))
        self.assertEqual(tuple(control.types.buttons), ('pdf',))
        self.assertTrue(control.matches_document({'medium': 'physical'}))
        self.assertTrue(control.matches_document({'medium': 'digital', 'format': 'application/pdf'}))

    def test_3687_silent_facet_reconciliation_updates_reset_availability(self):
        calls = []
        control = LocalQueryControls(lambda: calls.append(True), 'Filtre')
        control.set_choices(sources=(('paris', 'Paris'),))
        control.provider.set_values(('paris',))
        self.assertTrue(control.clear_button.sensitive)
        before = len(calls)
        control.set_choices(sources=())
        self.assertEqual(len(calls), before)
        self.assertFalse(control.clear_button.sensitive)

        control.set_document_choices(({'medium': 'digital', 'format': 'application/pdf'},))
        control.types.set_values(('pdf',))
        self.assertTrue(control.clear_button.sensitive)
        before = len(calls)
        control.set_document_choices(({'medium': 'physical'},))
        self.assertEqual(len(calls), before)
        self.assertFalse(control.clear_button.sensitive)

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
