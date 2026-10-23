"""Private, opt-in inspection of user declarations after provider sync.

No attested link is inferred from this list. Do not export its fields to
Diagnostics, plugins, public reports or a logged exception.
"""
from __future__ import annotations


_SOURCE_LABELS = {
    'numilog': 'Numilog',
    'kobo': 'Kobo',
    'paris': 'Bibliothèques de Paris',
}
_REVIEW_LABELS = {
    'recorded_unverified_file': 'Déclaré · fichier et droit non revérifiés',
    'source_or_right_changed': 'À examiner · droit ou compte modifié',
    'revoked': 'Révoqué · historique conservé',
}


class PrivateDeclarationHistory:
    """Bounded GTK list populated only when opened by the active reader."""

    def __init__(self, *, Gtk, session, alive):
        self.Gtk, self.session, self.alive = Gtk, session, alive
        self.offset = 0
        self.limit = 20
        self.expander = Gtk.Expander(label='Associations déclarées · historique privé')
        self.expander.set_name('reconciliation-declared-history')
        self.expander.set_expanded(False)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.expander.set_child(content)
        self.status = Gtk.Label(label=(
            'Déclarations personnelles, séparées des preuves fournisseur. '
            'Consulter n’atteste aucun droit et ne vérifie pas les fichiers.'
        ), xalign=0)
        self.status.set_wrap(True)
        content.append(self.status)
        self.include_revoked = Gtk.CheckButton(label='Inclure les déclarations révoquées')
        content.append(self.include_revoked)
        self.listing = Gtk.ListBox()
        self.listing.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_min_content_height(72)
        self.scroller.set_max_content_height(160)
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_child(self.listing)
        content.append(self.scroller)
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.previous = Gtk.Button(label='◀ Déclarations')
        self.next = Gtk.Button(label='Déclarations ▶')
        self.refresh_button = Gtk.Button(label='Actualiser les déclarations')
        for button in (self.previous, self.next, self.refresh_button):
            nav.append(button)
        content.append(nav)
        self.detail = Gtk.Label(label='Sélectionnez une déclaration.', xalign=0)
        self.detail.set_wrap(True)
        self.detail.set_selectable(True)
        self.detail.set_max_width_chars(72)
        content.append(self.detail)
        self.expander.connect('notify::expanded', self._on_expanded)
        self.include_revoked.connect('toggled', self._on_history_changed)
        self.previous.connect('clicked', lambda *_: self._move(-self.limit))
        self.next.connect('clicked', lambda *_: self._move(self.limit))
        self.refresh_button.connect('clicked', lambda *_: self.refresh())
        self.listing.connect('row-selected', self._select)
        self.previous.set_sensitive(False)
        self.next.set_sensitive(False)

    def _clear(self):
        child = self.listing.get_first_child()
        while child is not None:
            self.listing.remove(child)
            child = self.listing.get_first_child()
        self.detail.set_text('Sélectionnez une déclaration.')

    def _on_expanded(self, widget, *_):
        if widget.get_expanded():
            self.refresh()
        else:
            # Retire the private account/file fields from hidden GTK widgets.
            self._clear()

    def _on_history_changed(self, *_):
        self.offset = 0
        if self.expander.get_expanded():
            self.refresh()

    def _move(self, delta):
        self.offset = max(0, self.offset + delta)
        self.refresh()

    def _select(self, _listing, row):
        if row is None or not self.alive() or not self.session.valid():
            self.detail.set_text('Sélectionnez une déclaration.')
            return
        item = getattr(row, '_local_declaration', None)
        if item is None:
            return
        source = _SOURCE_LABELS.get(item.provider_id, 'Source non identifiée')
        state = _REVIEW_LABELS.get(item.review, 'État non vérifié')
        self.detail.set_text(
            'Origine : déclaration personnelle (non attestée)\n'
            + 'Source désignée : ' + source + '\n'
            + 'Compte : ' + item.account_id + '\n'
            + 'Édition : ' + item.item_id + '\n'
            + 'Fichier enregistré : ' + item.file_path + '\n'
            + 'État : ' + state + '\n'
            + 'Aucune vérification de fichier ou de droit n’est produite par cette lecture.'
        )

    def refresh(self):
        if not self.expander.get_expanded():
            return
        if not self.alive() or not self.session.valid():
            self._clear()
            self.status.set_text('Inspection expirée : relancez la réconciliation.')
            self.previous.set_sensitive(False)
            self.next.set_sensitive(False)
            return
        page = self.session.declared_links_page(
            offset=self.offset, limit=self.limit,
            include_revoked=self.include_revoked.get_active(),
        )
        self._clear()
        if page is None:
            self.status.set_text(
                'Historique non consultable pour cette identité. '
                'Aucune conclusion sur les associations enregistrées.'
            )
            self.previous.set_sensitive(False)
            self.next.set_sensitive(False)
            return
        self.status.set_text(
            f'{page.total} déclaration(s) pour ce profil · '
            'déclarations non attestées · aucune écriture'
        )
        for item in page.rows:
            row = self.Gtk.ListBoxRow()
            row._local_declaration = item
            source = _SOURCE_LABELS.get(item.provider_id, 'Source non identifiée')
            state = _REVIEW_LABELS.get(item.review, 'À examiner')
            label = self.Gtk.Label(
                label=source + ' · ' + item.item_id + ' · ' + state,
                xalign=0,
            )
            label.set_wrap(True)
            row.set_child(label)
            self.listing.append(row)
        self.previous.set_sensitive(self.offset > 0)
        self.next.set_sensitive(page.limited)
        if page.rows:
            self.listing.select_row(self.listing.get_row_at_index(0))
        elif page.total == 0:
            self.detail.set_text('Aucune déclaration dans ce filtre. '
                                 'Cela ne signifie pas qu’aucun livre existe.')
