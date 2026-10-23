"""Private, opt-in inspection of user declarations after provider sync.

No attested link is inferred from this list. Do not export its fields to
Diagnostics, plugins, public reports or a logged exception.
"""
from __future__ import annotations

from ..task_queue import TaskPriority


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

    def __init__(self, *, Gtk, Adw, window, runner, session, alive):
        self.Gtk, self.Adw, self.window, self.runner = Gtk, Adw, window, runner
        self.session, self.alive = session, alive
        self.generation = 0
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
        self.withdraw_button = Gtk.Button(label='Révoquer cette déclaration…')
        self.withdraw_button.set_tooltip_text(
            'Prévisualiser puis confirmer le retrait de cette seule déclaration, '
            'y compris si l’ancien fichier ou compte a disparu.'
        )
        self.withdraw_button.set_sensitive(False)
        content.append(self.withdraw_button)
        self.withdraw_button.connect('clicked', lambda *_: self._preview_withdrawal())
        self.expander.connect('notify::expanded', self._on_expanded)
        self.include_revoked.connect('toggled', self._on_history_changed)
        self.previous.connect('clicked', lambda *_: self._move(-self.limit))
        self.next.connect('clicked', lambda *_: self._move(self.limit))
        self.refresh_button.connect('clicked', lambda *_: self.refresh())
        self.listing.connect('row-selected', self._select)
        self.previous.set_sensitive(False)
        self.next.set_sensitive(False)

    def _clear(self):
        self.generation += 1
        self.withdraw_button.set_sensitive(False)
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
        self.generation += 1
        self.withdraw_button.set_sensitive(False)
        if row is None or not self.alive() or not self.session.valid():
            self.detail.set_text('Sélectionnez une déclaration.')
            return
        item = getattr(row, '_local_declaration', None)
        if item is None:
            return
        self.withdraw_button.set_sensitive(item.state == 'active')
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


    def _selected(self):
        row = self.listing.get_selected_row()
        return getattr(row, '_local_declaration', None) if row is not None else None

    def _current(self, relation_id, generation, *, fresh=True):
        item = self._selected()
        return (self.alive() and self.expander.get_expanded()
                and self.generation == generation
                and item is not None and item.relation_id == relation_id
                and (not fresh or self.session.valid()))

    def _preview_withdrawal(self):
        item = self._selected()
        if (item is None or item.state != 'active'
                or not self.alive() or not self.session.valid()):
            return
        relation_id, generation = item.relation_id, self.generation
        self.withdraw_button.set_sensitive(False)
        self.status.set_text('Vérification locale du retrait… Aucune écriture.')

        def read():
            return self.session.preview_history_withdrawal(
                relation_id,
                # Worker code may check a scalar generation only; never
                # inspect GTK widgets from a worker thread. Core independently
                # revalidates the active identity, scope and SQLite state.
                cancelled=lambda: self.generation != generation,
            )

        def completed(result):
            if not self._current(relation_id, generation):
                return
            status = result.get('status') if type(result) is dict else None
            if status == 'revoke_preview':
                self._confirm_withdrawal(item, generation)
            else:
                self.withdraw_button.set_sensitive(True)
                self.status.set_text('Révocation indisponible ou déjà effectuée. '
                                     'Aucune modification.')

        def failed(_error):
            if self._current(relation_id, generation):
                self.withdraw_button.set_sensitive(True)
                self.status.set_text('Prévisualisation indisponible. Aucune modification.')

        self.runner.submit('Prévisualisation du retrait d’une déclaration', read,
                           completed, failed, priority=TaskPriority.MAINTENANCE,
                           kind='maintenance-inspection')

    def _confirm_withdrawal(self, item, generation):
        relation_id = item.relation_id
        if not self._current(relation_id, generation):
            return
        source = _SOURCE_LABELS.get(item.provider_id, 'Source non identifiée')
        # The user explicitly opted into this *private* dialog. Never use
        # markup, diagnostics or a serialized action token for these fields.
        body = (
            'Profil : ' + item.identity_id + '\n'
            + 'Source déclarée : ' + source + '\n'
            + 'Compte : ' + item.account_id + '\n'
            + 'Édition : ' + item.item_id + '\n'
            + 'Fichier enregistré : ' + item.file_path + '\n\n'
            + 'Modification : révoquer cette déclaration personnelle '
            'et conserver son historique. Aucun fichier, droit fournisseur, '
            'reçu ou placement ne sera supprimé ou recréé.'
        )
        dialog = self.Adw.MessageDialog.new(
            self.window, 'Retirer cette déclaration ?', body,
        )
        dialog.set_body_use_markup(False)
        dialog.add_response('cancel', 'Annuler')
        dialog.add_response('apply', 'Révoquer la déclaration')
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def response(_dialog, response_id):
            if not self._current(relation_id, generation):
                return
            if response_id != 'apply':
                self.session.revoke_manual_attachment_preview()
                self.withdraw_button.set_sensitive(True)
                self.status.set_text('Révocation annulée. Aucune écriture.')
                return
            self.withdraw_button.set_sensitive(False)
            self.status.set_text('Confirmation reçue · vérification finale…')

            def write():
                return self.session.confirm_history_withdrawal(
                    relation_id, user_confirmed=True,
                    # Worker code may check a scalar generation only; never
                # inspect GTK widgets from a worker thread. Core independently
                # revalidates the active identity, scope and SQLite state.
                cancelled=lambda: self.generation != generation,
                )

            def completed(result):
                if not self._current(relation_id, generation, fresh=False):
                    return
                status = result.get('status') if type(result) is dict else None
                if status in ('revoked', 'already_revoked'):
                    if self.session.valid():
                        self.refresh()
                    else:
                        self.withdraw_button.set_sensitive(False)
                    self.status.set_text('Déclaration révoquée · historique conservé. '
                                         'Fichiers et droits inchangés.')
                else:
                    self.withdraw_button.set_sensitive(self.session.valid())
                    self.status.set_text('Révocation refusée. Aucune suppression '
                                         'de fichier ou de droit.')

            def failed(_error):
                if self._current(relation_id, generation, fresh=False):
                    self.withdraw_button.set_sensitive(self.session.valid())
                    self.status.set_text('Révocation indisponible. '
                                         'Relancez la vérification privée.')

            self.runner.submit('Confirmation du retrait d’une déclaration',
                               write, completed, failed,
                               priority=TaskPriority.MAINTENANCE,
                               kind='maintenance-inspection')

        dialog.connect('response', response)
        dialog.present()
