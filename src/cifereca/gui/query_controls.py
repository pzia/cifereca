from gi.repository import Gtk
from .query_model import QueryState, FIELDS
from .book_facets import CONTENT_FIELDS

class MultiSelect(Gtk.MenuButton):
    def __init__(self, label, choices, changed):
        super().__init__()
        self.caption = label
        self.buttons = {}
        self.set_label(label + ' : Tous')
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(10); box.set_margin_end(10)
        reset = Gtk.Button(label='Tous')
        reset.connect('clicked', lambda _b: self.set_values(()))
        box.append(reset)
        for value, title in choices:
            button = Gtk.CheckButton(label=title)
            self.buttons[value] = button
            button.connect('toggled', lambda _b: self._changed(changed))
            box.append(button)
        pop.set_child(box); self.set_popover(pop)

    def _changed(self, changed):
        count = len(self.values())
        self.set_label(f'{self.caption} : {count}' if count else self.caption + ' : Tous')
        changed()

    def values(self):
        return tuple(key for key, button in self.buttons.items() if button.get_active())

    def set_values(self, values):
        for key, button in self.buttons.items():
            button.set_active(key in values)

class QueryControls(Gtk.Box):
    def __init__(self, providers, changed, activate, *, library=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_hexpand(True)
        self.primary = Gtk.Box(spacing=8); self.append(self.primary)
        self.entry = Gtk.SearchEntry(); self.entry.set_hexpand(True)
        self.entry.set_placeholder_text('Filtre' if library else 'Recherche dans le connu')
        self.entry.connect('search-changed', lambda _e: changed())
        self.entry.connect('activate', lambda _e: activate())
        self.primary.append(self.entry)
        flow = Gtk.FlowBox(); flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_min_children_per_line(1); flow.set_max_children_per_line(4)
        flow.set_row_spacing(6); flow.set_column_spacing(6); self.append(flow)

        def insert_filter(widget):
            flow.insert(widget, -1)
            child = widget.get_parent()
            if isinstance(child, Gtk.FlowBoxChild):
                child.set_focusable(False)
            return widget

        self.provider = insert_filter(MultiSelect('Forme · Sources', providers, changed))
        self.types = insert_filter(MultiSelect('Forme · Formats', [('epub','EPUB'),('pdf','PDF'),('mp3','MP3'),('m4a','M4A'),('ogg','OGG'),('opus','OPUS'),('flac','FLAC'),('wav','WAV')], changed))
        self.languages = insert_filter(MultiSelect('Langues', [('fr','Français'),('en','English'),('de','Deutsch'),('es','Español'),('it','Italiano'),('pt','Português'),('nl','Nederlands')], changed))
        self.field = insert_filter(Gtk.DropDown.new_from_strings(['Fond / forme · Tous','Fond · Titre','Fond · Auteur','Forme · ISBN','Fond · Sujet','Fond · Série','Fond · Collection','Fond · Langue']))
        self.field.set_tooltip_text("Fond : titre, auteur, sujet, série, collection. Forme : ISBN, source, format.")
        self.field.connect('notify::selected', lambda *_: changed())
        self.scope = insert_filter(Gtk.DropDown.new_from_strings(['Tous','Physiques','Numériques']))
        self.scope.set_selected(2)
        self.scope.connect('notify::selected', lambda *_: changed())

    def state(self):
        return QueryState(self.entry.get_text().strip(), FIELDS[self.field.get_selected()],
                          ('all','physical','digital')[self.scope.get_selected()], self.provider.values(), self.types.values(), self.languages.values())

    def set_state(self, state):
        self.entry.set_text(state.text); self.field.set_selected(FIELDS.index(state.field))
        self.scope.set_selected(('all','physical','digital').index(state.scope))
        self.provider.set_values(state.providers); self.types.set_values(state.types); self.languages.set_values(getattr(state, 'languages', ()))
