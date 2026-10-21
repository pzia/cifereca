"""Private local inspection UI. No export, provider call or repair action."""
from __future__ import annotations

from ..core.library_local_inspection import LocalInspection, PROVIDERS, STATES, open_local_inspection
from ..task_queue import TaskPriority
from .maintenance_tools import MaintenanceCancelControl


# Core-only closed failure causes: never expose private source text in GTK.
_INSPECTION_FAILURE_MESSAGES = {
    'snapshot_time_budget': (
        'La lecture des preuves SQLite a dépassé sa durée maximale. '
        'L’inspection n’a pas pu vérifier les livres : cela ne prouve pas '
        'leur absence. Les traces Réconciliation indiquent la phase concernée.'
    ),
    'snapshot_wal_reader_unavailable': (
        'La lecture sécurisée de la base SQLite active (WAL) est indisponible. '
        'Aucun constat sur les livres ne peut être établi.'
    ),
    'snapshot_reader_unavailable': (
        'La lecture sécurisée de la base SQLite est indisponible. '
        'Aucun constat sur les livres ne peut être établi.'
    ),
    'snapshot_unavailable': (
        'Les preuves SQLite locales sont indisponibles. '
        'Les livres non vérifiés ne sont pas réputés absents.'
    ),
    'snapshot_changed': (
        'La base SQLite a changé pendant la lecture. '
        'L’inspection est incomplète ; aucun livre n’est réputé absent.'
    ),
    'snapshot_identity_migration': (
        'Une migration des identités doit être terminée avant '
        'de pouvoir inspecter les preuves locales.'
    ),
    'snapshot_cancelled': 'La lecture des preuves SQLite a été interrompue.',
    'audit_cancelled': 'L’inspection locale a été interrompue.',
    'reconciliation_scope_changed': (
        'L’identité active ou sa configuration a changé pendant l’inspection. '
        'Relancez l’analyse sous l’identité voulue.'
    ),
    'reconciliation_identity_not_active': (
        'L’identité sélectionnée n’est plus active. '
        'Sélectionnez-la avant de relancer l’inspection.'
    ),
    'unknown_reconciliation_identity': (
        'L’identité sélectionnée n’est plus disponible. '
        'Vérifiez l’identité active.'
    ),
}
_INSPECTION_FALLBACK = (
    'Inspection locale indisponible ou incomplète. '
    'Les preuves non vérifiées ne signifient pas que les livres sont absents.'
)


def _inspection_failure_message(result: object) -> str:
    """Project one validated Core reason, never untrusted diagnostic prose.

    Multiple concurrent causes or malformed/foreign envelopes fail closed:
    a single convenient reason must not override another unknown failure.
    """
    if (type(result) is not dict or type(result.get('schema')) is not int
            or result.get('schema') != 1
            or result.get('read_only') is not True
            or result.get('acquisition_authorized') is not False
            or result.get('repair_authorized') is not False
            or result.get('status') != 'incomplete_scan'):
        return _INSPECTION_FALLBACK
    reasons = result.get('reasons')
    if (type(reasons) is not list or len(reasons) != 1
            or type(reasons[0]) is not str):
        return _INSPECTION_FALLBACK
    return _INSPECTION_FAILURE_MESSAGES.get(reasons[0], _INSPECTION_FALLBACK)


class LocalInspectionController:
    """GTK owner of a cancellable fresh read and its revocable local window."""

    def __init__(self, *, parent, Gtk, Adw, Pango, GLib, alive):
        self.parent, self.Gtk, self.Adw, self.Pango, self.GLib = parent, Gtk, Adw, Pango, GLib
        self.alive = alive
        self.control = self.dialog = self.session = None
        self._generation = 0
        self._watch = None

    def clear(self):
        self._generation += 1
        if self.control is not None:
            self.control.cancel()
            self.control = None
        if self.session is not None:
            self.session.close()
            self.session = None
        if self._watch is not None:
            self.GLib.source_remove(self._watch)
            self._watch = None
        if self.dialog is not None:
            dialog, self.dialog = self.dialog, None
            dialog.destroy()

    def start(self):
        self.clear()
        if not self.alive():
            return
        identity = self.parent.service.current_identity
        if identity is None:
            return
        generation = self._generation
        control = self.control = MaintenanceCancelControl()
        dialog = self.Adw.MessageDialog.new(self.parent, 'Inspection locale',
            'Nouvelle lecture hors réseau des fichiers et preuves de l’identité active. Aucun changement.')
        dialog.add_response('cancel', 'Annuler')
        dialog.connect('response', lambda *_: self.clear() if self.dialog is dialog else None)
        self.dialog = dialog
        dialog.present()

        def read():
            try:
                return open_local_inspection(self.parent.service, identity_id=identity.identity_id, control=control)
            except Exception:
                raise RuntimeError('Inspection locale indisponible.') from None

        def completed(session):
            if generation != self._generation or control.cancelled() or not self.alive():
                if isinstance(session, LocalInspection):
                    session.close()
                return
            self.control = None
            if self.dialog is dialog:
                self.dialog = None
                dialog.destroy()
            if not isinstance(session, LocalInspection):
                self.parent.show_error(_inspection_failure_message(session), 'Inspection locale')
                return
            if not session.valid():
                # Unlike a failed snapshot envelope, an established private
                # session can really be invalidated by the active scope guard.
                self.parent.show_error(
                    _INSPECTION_FAILURE_MESSAGES['reconciliation_scope_changed'],
                    'Inspection locale',
                )
                return
            self.session = session
            self.present()

        def failed(_error):
            if generation == self._generation and self.alive():
                self.clear()
                self.parent.show_error('Inspection locale indisponible. Relancez la réconciliation.', 'Inspection locale')

        self.parent.runner.submit('Inspection locale de bibliothèque', read, completed, failed,
                                  priority=TaskPriority.MAINTENANCE, kind='maintenance-inspection')

    def present(self):
        Gtk, Adw, Pango = self.Gtk, self.Adw, self.Pango
        session = self.session
        window = Adw.Window(transient_for=self.parent, modal=True, title='Réconciliation · cas locaux')
        window.set_default_size(920, 620)
        self.dialog = window
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for edge in ('top', 'bottom', 'start', 'end'):
            getattr(box, 'set_margin_'+edge)(12)
        notice = Gtk.Label(label='Identité active uniquement · détails privés locaux · aucun rattachement ni téléchargement', xalign=0)
        notice.set_wrap(True)
        box.append(notice)
        summary = session.summary()
        if summary.get('status') != 'complete' or summary.get('inspection_limited'):
            warning = Gtk.Label(label='Analyse ou affichage incomplet : certaines preuves restent non vérifiées. Un livre non vérifié n’est pas réputé absent.', xalign=0)
            warning.set_wrap(True)
            box.append(warning)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        provider = Gtk.DropDown.new_from_strings(list(PROVIDERS.values()))
        provider.set_tooltip_text('Filtrer par fournisseur attesté pour les fichiers ; source déclarée pour les droits')
        state = Gtk.DropDown.new_from_strings(list(STATES.values()))
        state.set_tooltip_text('Filtrer par état de preuve')
        toolbar.append(provider); toolbar.append(state); box.append(toolbar)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_vexpand(True)
        listing = Gtk.ListBox()
        listing.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_width(260)
        scroll.set_child(listing)
        paned.set_start_child(scroll)
        detail = Gtk.Label(label='Sélectionnez un cas pour consulter ses preuves.', xalign=0, yalign=0)
        detail.set_wrap(True)
        detail.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        detail.set_selectable(True)
        detail.set_max_width_chars(72)
        detail_scroll = Gtk.ScrolledWindow()
        detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        detail_scroll.set_child(detail)
        paned.set_end_child(detail_scroll)
        paned.set_position(300)
        box.append(paned)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        previous, following = Gtk.Button(label='Précédent'), Gtk.Button(label='Suivant')
        count = Gtk.Label(xalign=0)
        count.set_hexpand(True)
        close = Gtk.Button(label='Fermer')
        for widget in (previous, count, following, close): footer.append(widget)
        box.append(footer)
        window.set_content(box)
        offset = [0]
        current_total = [0]

        def valid():
            if self.session is session and self.alive() and session.valid():
                return True
            self.clear()
            return False

        def select(_listing, row):
            if row is None or not valid():
                return
            case = session.detail(row._local_case_key)
            if case is not None:
                states = ', '.join(STATES[name] for name in case.states)
                detail.set_text(PROVIDERS[case.provider]+' · '+states+'\n\n'+case.details)

        def render(*_args, reset=False):
            if not valid():
                return
            if reset: offset[0] = 0
            page = session.page(provider=list(PROVIDERS)[provider.get_selected()],
                                state=list(STATES)[state.get_selected()], offset=offset[0])
            current_total[0] = page.total
            child = listing.get_first_child()
            while child is not None:
                listing.remove(child)
                child = listing.get_first_child()
            detail.set_text('Sélectionnez un cas pour consulter ses preuves.' if page.total else 'Aucun cas dans ces filtres. Ce résultat ne prouve pas l’absence d’un livre.')
            for case in page.cases:
                row = Gtk.ListBoxRow()
                row._local_case_key = case.key
                label = Gtk.Label(label=case.label, xalign=0)
                label.set_wrap(True)
                label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                row.set_child(label)
                listing.append(row)
            count.set_text(f'{offset[0]+1 if page.cases else 0}–{offset[0]+len(page.cases)} / {page.total}'
                           + (' · affichage limité' if page.limited else ''))
            previous.set_sensitive(offset[0] > 0)
            following.set_sensitive(offset[0]+len(page.cases) < page.total)

        def move(delta):
            offset[0] = max(0, min(offset[0]+delta, max(0, current_total[0]-1)))
            render()

        listing.connect('row-selected', select)
        provider.connect('notify::selected', lambda *_: render(reset=True))
        state.connect('notify::selected', lambda *_: render(reset=True))
        previous.connect('clicked', lambda *_: move(-50))
        following.connect('clicked', lambda *_: move(50))
        close.connect('clicked', lambda *_: self.clear())
        window.connect('close-request', lambda *_: (self.clear(), False)[1])
        render()
        if self.dialog is window:
            self._watch = self.GLib.timeout_add_seconds(1, valid)
            window.present()
