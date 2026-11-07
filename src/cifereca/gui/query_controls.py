from gi.repository import Gtk
from .query_model import QueryState, FIELDS
from .book_facets import CONTENT_FIELDS
from ..core.provider_search import provider_search_summary
from ..core.document_filter import loaded_document_facets

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
        # A single reset for Search and every local consumer of this shared
        # strip. Only the owning view decides how to react to the change.
        self.clear_button = Gtk.Button(label='Effacer les filtres')
        self.clear_button.set_tooltip_text(
            'Revenir aux filtres initiaux de cette vue'
        )
        self.clear_button.connect('clicked', lambda *_: self.reset_filters())
        self.primary.append(self.clear_button)
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
        self._scope_values = ('all','physical','digital','unknown')
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
        # A read-only provider status is distinct from selectable, live sources.
        self.provider_status_notice = Gtk.Label(xalign=0)
        self.provider_status_notice.set_wrap(True)
        self.provider_status_notice.add_css_class('caption')
        self.provider_status_notice.add_css_class('dim-label')
        self.provider_status_notice.set_visible(False)
        self.append(self.provider_status_notice)
        self._last_notified_state = self.state()
        self._initial_filter_state = self._last_notified_state
        self._sync_clear_button()

    def _sync_clear_button(self):
        self.clear_button.set_sensitive(self.state() != self._initial_filter_state)

    def reset_filters(self):
        """Reset all view filters atomically to this control's initial scope.

        Search defaults to digital; local views override their initial
        state to all loaded documents. No provider I/O is performed here.
        """
        self.set_state(self._initial_filter_state)

    def set_provider_choices(self, choices):
        self.provider.set_choices(choices)

    def set_provider_status_notice(self, message):
        message = str(message or '')
        self.provider_status_notice.set_label(message)
        self.provider_status_notice.set_visible(bool(message))

    def set_provider_search_capabilities(self, capabilities):
        self._provider_search_capabilities = dict(capabilities or {})
        self._update_search_capability_hint()

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
        self._sync_clear_button()
        if state == self._last_notified_state:
            return
        self._last_notified_state = state
        self._changed_callback()

    def state(self):
        return QueryState(self.entry.get_text().strip(), FIELDS[self.field.get_selected()],
                          self._scope_values[self.scope.get_selected()], self.provider.values(), self.types.values(), self.languages.values())

    def set_state(self, state, *, notify=True):
        self._programmatic_update = True
        try:
            self.entry.set_text(state.text); self.field.set_selected(FIELDS.index(state.field))
            self.scope.set_selected(self._scope_values.index(state.scope))
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
        self._sync_clear_button()


class LocalQueryControls(QueryControls):
    """Reuse the Search controls for local filtering of an existing list."""

    def __init__(self, changed, placeholder):
        super().__init__((), changed, self._changed, library=True)
        self.entry.set_placeholder_text(placeholder)
        self.field.set_visible(False)
        self._local_field_enabled = False
        self._format_labels = self.types.choice_labels.copy()
        # Only a completed local result may attest a choice across a
        # provisional top-N. New profiles / failed calculations clear it.
        self._attested_document_scopes = frozenset()
        self._attested_document_formats = frozenset()
        scope_model = self.scope.get_model()
        self._scope_labels = {
            value: scope_model.get_string(index)
            for index, value in enumerate(self._scope_values)
        }
        self.set_document_choices(())
        # No catalogue or language is selectable before the first loaded
        # projection. Search's global QueryControls remain unchanged.
        self.set_choices(sources=(), languages=())
        # A local projection starts with every loaded document visible,
        # unlike Search's default digital scope.
        self._initial_filter_state = self.state()
        self._sync_clear_button()

    @property
    def widget(self):
        return self

    def filter_text(self):
        return self.entry.get_text().strip()

    def enable_field_filter(self):
        """Expose Search's existing field chooser for a read-only local set.

        Disabled by default for installed consumers that use the historical
        title/author text filter. Opt-in does not trigger a remote search.
        """
        self._local_field_enabled = True
        self.field.set_visible(True)

    def filter_field(self):
        """Selected Search field, or 'all' when this local view has no chooser."""
        if not self._local_field_enabled:
            return 'all'
        try:
            index = self.field.get_selected()
            return FIELDS[index] if type(index) is int and 0 <= index < len(FIELDS) else '\x00'
        except (AttributeError, IndexError, TypeError, ValueError):
            return '\x00'

    def selected_sources(self):
        return self.provider.values()

    def selected_languages(self):
        return self.languages.values()

    def _document_facets(self, item):
        return loaded_document_facets(
            item, tuple(self._format_labels),
        )

    def set_document_choices(self, items, *, provisional=False):
        """Refresh local facets without dropping attested filters on a partial head.

        A provisional ranked top-N is not an exhaustive catalogue response.
        Retain only the previously selected Format/Support from a completed
        projection; the final result (or identity reset) removes absent ones.
        This method never creates a provider scope, right or remote query.
        """
        media, formats = set(), set()
        for item in items:
            medium, offered = self._document_facets(item)
            media.add(medium)
            formats.update(offered)
        known_medium = bool(media - {'unknown'})
        selected_scope = self._scope_values[self.scope.get_selected()]
        selected_formats = set(self.types.values())
        if provisional:
            if selected_scope in self._attested_document_scopes:
                media.add(selected_scope)
                known_medium = True
            formats.update(selected_formats & self._attested_document_formats)
        else:
            # Unknown is selectable alongside a positively known medium;
            # keep that explicit choice as well across partial heads.
            self._attested_document_scopes = frozenset(media if known_medium else ())
            self._attested_document_formats = frozenset(formats)
        scope_choices = tuple(
            (value, label) for value, label in self._scope_labels.items()
            if value == 'all' or (known_medium and value in media)
        )
        scope_values = tuple(value for value, _label in scope_choices)
        self._programmatic_update = True
        try:
            if scope_values != self._scope_values:
                self.scope.set_model(Gtk.StringList.new([label for _value, label in scope_choices]))
                self._scope_values = scope_values
                self.scope.set_selected(scope_values.index(selected_scope) if selected_scope in scope_values else 0)
            self.scope.set_visible(known_medium)
            self.types.set_choices(tuple(
                (value, label) for value, label in self._format_labels.items() if value in formats
            ))
            self.types.set_visible(bool(formats))
        finally:
            self._programmatic_update = False
        self._last_notified_state = self.state()
        self._sync_clear_button()

    def active_document_labels(self):
        """Optional public summary of currently active local document filters.

        Return only labels from Core's available/attested facet choices.
        The caller may display these alongside counts; this never filters,
        queries a provider, rescales recommendations or infers permissions.
        """
        try:
            scope = self._scope_values[self.scope.get_selected()]
            formats = self.types.values()
            if type(formats) is not tuple:
                return ()
            labels = []
            if self._local_field_enabled:
                chosen = self.filter_field()
                if chosen != 'all' and chosen in FIELDS:
                    labels.append(self.field.get_model().get_string(FIELDS.index(chosen)))
            if scope != 'all':
                label = self._scope_labels.get(scope)
                if type(label) is str and label:
                    labels.append(label)
            for value in formats:
                label = self._format_labels.get(value)
                if type(label) is str and label:
                    labels.append(label)
            return tuple(labels)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return ()

    def matches_document(self, item):
        scope = self._scope_values[self.scope.get_selected()]
        formats = self.types.values()
        if scope == 'all' and not formats:
            return True
        medium, offered = self._document_facets(item)
        return ((scope == 'all' or scope == medium)
                and (not formats or not set(formats).isdisjoint(offered)))

    def set_choices(self, *, sources=None, languages=None):
        self._programmatic_update = True
        try:
            if sources is not None:
                self.set_provider_choices(sources)
                # An empty facet is not an available filter: do not display
                # a popover containing only 'Tous'. Keep selections while
                # valid choices survive a progressive result refresh.
                self.provider.set_visible(bool(self.provider.buttons))
            if languages is not None:
                self.languages.set_choices(languages)
                self.languages.set_visible(bool(self.languages.buttons))
        finally:
            self._programmatic_update = False
        self._last_notified_state = self.state()
        self._sync_clear_button()
