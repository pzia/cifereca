from gi.repository import Gtk
from .query_model import QueryState, FIELDS
from .book_facets import CONTENT_FIELDS
from ..core.provider_search import provider_search_summary

class MultiSelect(Gtk.MenuButton):
    def __init__(self, label, choices, changed, help_text=''):
        super().__init__()
        self.caption = label
        choices = tuple(choices)
        self.choice_labels = dict(choices)
        self.buttons = {}
        self._changed_callback = changed
        self._programmatic_update = False
        self._last_notified_values = ()
        self.help_text = str(help_text or '')
        self.set_label(label)
        self.set_tooltip_text(self.help_text or None)
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(10); box.set_margin_end(10)
        reset = Gtk.Button(label='Tous')
        reset.connect('clicked', lambda _b: self.set_values(()))
        box.append(reset)
        self._choices_box = box
        for value, title in choices:
            button = Gtk.CheckButton(label=title)
            self.buttons[value] = button
            button.connect('toggled', lambda _b: self._changed())
            box.append(button)
        pop.set_child(box); self.set_popover(pop)

    def _changed(self):
        if self._programmatic_update:
            return
        values = self.values()
        titles = [self.choice_labels.get(value, str(value)) for value in values]
        if not titles:
            summary = 'Tous'
        elif len(titles) == 1:
            summary = titles[0]
        elif len(titles) == 2 and sum(len(title) for title in titles) <= 22:
            summary = ' + '.join(titles)
        else:
            summary = f'{titles[0]} +{len(titles) - 1}'
        tooltip = ', '.join(titles) if titles else (self.help_text or None)
        self.set_label(self.caption if not titles else f'{self.caption} : {summary}')
        self.set_tooltip_text(tooltip)
        if values == self._last_notified_values:
            return
        self._last_notified_values = values
        self._changed_callback()

    def set_choices(self, choices):
        """Replace available sources without clearing surviving selections."""
        choices = tuple(choices)
        labels = dict(choices)
        if list(self.buttons) == list(labels) and self.choice_labels == labels:
            return
        selected = set(self.values())
        self._programmatic_update = True
        try:
            for button in self.buttons.values():
                self._choices_box.remove(button)
            self.buttons = {}
            self.choice_labels = labels
            for value, title in choices:
                button = Gtk.CheckButton(label=title)
                button.set_active(value in selected)
                button.connect('toggled', lambda _b: self._changed())
                self._choices_box.append(button)
                self.buttons[value] = button
        finally:
            self._programmatic_update = False
        self._changed()

    def values(self):
        return tuple(key for key, button in self.buttons.items() if button.get_active())

    def set_values(self, values):
        selected = set(values)
        self._programmatic_update = True
        try:
            for key, button in self.buttons.items():
                active = key in selected
                if button.get_active() != active:
                    button.set_active(active)
        finally:
            self._programmatic_update = False
        self._changed()

class QueryControls(Gtk.Box):
    def __init__(self, providers, changed, activate, *, library=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._changed_callback = changed
        self._programmatic_update = False
        self._provider_search_capabilities = {}
        self.set_hexpand(True)
        self.primary = Gtk.Box(spacing=8); self.append(self.primary)
        self.entry = Gtk.SearchEntry(); self.entry.set_hexpand(True)
        self.entry.set_placeholder_text('Filtre' if library else 'Recherche dans le connu')
        self.entry.connect('search-changed', lambda _e: self._changed())
        self.entry.connect('activate', lambda _e: activate())
        self.primary.append(self.entry)
        filter_scroller = Gtk.ScrolledWindow()
        filter_scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        filter_scroller.set_propagate_natural_height(True)
        filter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        filter_row.set_hexpand(False)
        filter_scroller.set_child(filter_row)
        self.append(filter_scroller)
        def insert_filter(widget):
            widget.set_hexpand(False)
            widget.set_halign(Gtk.Align.START)
            filter_row.append(widget)
            return widget

        self.provider = insert_filter(MultiSelect(
            'Source', providers, self._changed,
            'Limiter la vue à une ou plusieurs sources',
        ))
        self.types = insert_filter(MultiSelect(
            'Format',
            [('epub','EPUB'),('pdf','PDF'),('mp3','MP3'),('m4a','M4A'),('ogg','OGG'),('opus','OPUS'),('flac','FLAC'),('wav','WAV')],
            self._changed,
            'Limiter la vue à un ou plusieurs formats',
        ))
        self.languages = insert_filter(MultiSelect(
            'Langue',
            [('fr','Français'),('en','English'),('de','Deutsch'),('es','Español'),('it','Italiano'),('pt','Português'),('nl','Nederlands')],
            self._changed,
            'Limiter la vue à une ou plusieurs langues',
        ))
        self.field = insert_filter(Gtk.DropDown.new_from_strings([
            'Champ', 'Champ · Titre', 'Champ · Auteur', 'Champ · ISBN',
            'Champ · Sujet', 'Champ · Série', 'Champ · Collection', 'Champ · Langue',
        ]))
        self.field.set_tooltip_text("Choisir le champ interrogé : titre, auteur, ISBN, sujet, série, collection ou langue.")
        self.field.connect('notify::selected', lambda *_: self._changed())
        self.scope = insert_filter(Gtk.DropDown.new_from_strings([
            'Type', 'Type · Physique', 'Type · Numérique', 'Type · Support indéterminé',
        ]))
        self.scope.set_tooltip_text("Limiter aux documents physiques, numériques ou de type indéterminé.")
        self.scope.set_selected(2)
        self.scope.connect('notify::selected', lambda *_: self._changed())
        self.capability_hint = Gtk.Label(xalign=0)
        self.capability_hint.set_wrap(True)
        self.capability_hint.add_css_class('caption')
        self.capability_hint.add_css_class('dim-label')
        self.capability_hint.set_visible(False)
        self.append(self.capability_hint)
        self.availability_hint = Gtk.Label(xalign=0)
        self.availability_hint.set_wrap(True)
        self.availability_hint.add_css_class('caption')
        self.availability_hint.set_visible(False)
        self.append(self.availability_hint)
        self._last_notified_state = self.state()

    def set_provider_choices(self, choices):
        self.provider.set_choices(choices)

    def set_provider_search_capabilities(self, capabilities):
        self._provider_search_capabilities = dict(capabilities or {})
        self._update_search_capability_hint()

    def set_provider_availability_hint(self, text):
        # Read-only known catalogue state, not part of the source selector.
        self.availability_hint.set_label(str(text or ''))
        self.availability_hint.set_visible(bool(text))

    def _update_search_capability_hint(self):
        summary = provider_search_summary(
            self._provider_search_capabilities,
            self.provider.values(),
        )
        self.capability_hint.set_label(summary)
        self.capability_hint.set_visible(bool(summary))

    def _changed(self):
        if self._programmatic_update:
            return
        self._update_search_capability_hint()
        state = self.state()
        if state == self._last_notified_state:
            return
        self._last_notified_state = state
        self._changed_callback()

    def state(self):
        return QueryState(self.entry.get_text().strip(), FIELDS[self.field.get_selected()],
                          ('all','physical','digital','unknown')[self.scope.get_selected()], self.provider.values(), self.types.values(), self.languages.values())

    def set_state(self, state, *, notify=True):
        self._programmatic_update = True
        try:
            self.entry.set_text(state.text); self.field.set_selected(FIELDS.index(state.field))
            self.scope.set_selected(('all','physical','digital','unknown').index(state.scope))
            self.provider.set_values(state.providers); self.types.set_values(state.types); self.languages.set_values(getattr(state, 'languages', ()))
        finally:
            self._programmatic_update = False
        if notify:
            self._changed()
        else:
            self._last_notified_state = self.state()
            updater = getattr(self, "_update_search_capability_hint", None)
            if callable(updater):
                updater()
