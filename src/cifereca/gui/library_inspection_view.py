"""Private multi-source inspection, provider-specific confirmed local placement."""
from __future__ import annotations

from time import monotonic

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

# Only exact Core-defined phase names can be presented. Worker messages,
# paths, account names or arbitrary plugin prose never reach GTK.
_INSPECTION_PHASES = frozenset({
    'Inventaire local', 'Lecture des preuves locales',
    'Comparaison des preuves locales',
    'Comparaison des identifiants bibliographiques',
    'Vérification des chemins enregistrés', 'Audit local terminé',
})


def _toggle_inspection_maximize(window) -> None:
    """Only GTK window state changes, never a new read or a private export."""
    if window.is_maximized():
        window.unmaximize()
    else:
        window.maximize()


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
        self._activity_row = None
        self._activity_box = self._activity_label = None
        self._activity_clock = None
        self._activity_started = None
        self._activity_phase = 'Préparation'
        self._activity_files = None
        self._activity_mode = 'preparing'

    def attach_activity_row(self, row):
        """The Maintenance ActionRow owns the nonmodal loading indicator."""
        self._activity_row = row

    def _set_activity_state(self, message):
        """One fixed local inspection status, independent of last audit."""
        if (self._activity_row is not None
                and hasattr(self._activity_row, 'set_subtitle') and self.alive()):
            self._activity_row.set_subtitle(message)

    def _show_activity(self):
        if self._activity_row is None or not hasattr(self.Gtk, 'Box'):
            return
        if self._activity_box is None:
            Gtk = self.Gtk
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = Gtk.Label(label='Préparation des cas locaux…', xalign=0)
            # Long phase + mode + elapsed counters must not enlarge the
            # Maintenance action beyond a narrow GTK window.
            if hasattr(label, 'set_wrap'):
                label.set_wrap(True)
                label.set_max_width_chars(48)
            if hasattr(label, 'set_hexpand'):
                label.set_hexpand(True)
            cancel = Gtk.Button(label='Annuler')
            cancel.set_tooltip_text('Interrompre l’inspection locale en lecture seule')
            cancel.connect('clicked', lambda *_: self.clear())
            line.append(label)
            line.append(cancel)
            self._activity_row.add_suffix(line)
            self._activity_box, self._activity_label = line, label
        self._activity_label.set_label('Préparation des cas locaux…')
        self._set_activity_state(
            'Préparation des cas : cache privé ou nouvelle inspection locale '
            '(jusqu’à 600 s) · lecture seule · couverture inconnue.'
        )
        self._activity_box.set_visible(True)

    def _hide_activity(self):
        if self._activity_clock is not None:
            self.GLib.source_remove(self._activity_clock)
            self._activity_clock = None
        self._activity_started = None
        self._activity_files = None
        self._activity_mode = 'preparing'
        if self._activity_box is not None:
            self._activity_box.set_visible(False)

    def _render_activity(self):
        """Only closed phase and measured local counts/timer enter GTK."""
        if self._activity_started is None or self._activity_label is None:
            return
        elapsed = min(36_000, max(0, int(monotonic() - self._activity_started)))
        minutes, seconds = divmod(elapsed, 60)
        state = {
            'preparing': 'préparation : cache privé ou nouveau scan (jusqu’à 600 s)',
            'cached': 'preuves réutilisées (cache privé valide)',
            'new_scan': 'nouveau scan local hors réseau (jusqu’à 600 s) · couverture inconnue',
        }.get(self._activity_mode, 'préparation')
        message = f'Inspection détaillée · {state} · {self._activity_phase} · {minutes:02}:{seconds:02} écoulées'
        if self._activity_files is not None:
            message += f' · {self._activity_files} fichiers observés'
        self._activity_label.set_label(message)

    def _tick_activity(self, generation):
        if (generation != self._generation or self.control is None
                or self.control.cancelled() or not self.alive()):
            return False
        self._render_activity()
        return True

    def _mode(self, generation, code):
        """Only Core's exact local cache decision, never provider prose."""
        if code not in ('cached', 'new_scan'):
            return
        idle_add = getattr(self.GLib, 'idle_add', None)
        if idle_add is None:
            return

        def publish():
            if (generation == self._generation and self.control is not None
                    and not self.control.cancelled() and self.alive()):
                self._activity_mode = code
                self._set_activity_state(
                    'Réutilisation du cache privé validé.'
                    if code == 'cached' else
                    'Nouvelle inspection locale hors réseau (jusqu’à 600 s) · '
                    'lecture seule · couverture inconnue.'
                )
                self._render_activity()
            return False

        idle_add(publish)

    def _progress(self, generation, current, total, phase):
        if phase not in _INSPECTION_PHASES:
            return
        idle_add = getattr(self.GLib, 'idle_add', None)
        if idle_add is None:
            return

        def publish():
            if (generation == self._generation and self.control is not None
                    and not self.control.cancelled() and self.alive()
                    and self._activity_label is not None):
                self._activity_phase = phase
                # The broker reports the number of *observed* local files,
                # never an exhaustive total. Reject booleans, strings,
                # unbounded or decreasing counters from hostile producers.
                if type(current) is int and 0 <= current <= 100_000:
                    self._activity_files = max(
                        self._activity_files or 0, current,
                    )
                self._render_activity()
            return False

        idle_add(publish)

    def clear(self):
        running = self.control is not None and self._activity_started is not None
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
        self._hide_activity()
        if running:
            self._set_activity_state(
                'Inspection interrompue · aucun constat d’absence de livre.'
            )

    def start(self):
        self.clear()
        if not self.alive():
            return
        identity = self.parent.service.current_identity
        if identity is None:
            return
        generation = self._generation
        control = self.control = MaintenanceCancelControl(
            on_progress=lambda current, total, message:
                self._progress(generation, current, total, message),
        )
        # A valid one-shot private session may return immediately. Show only
        # unobtrusive inline activity, never a modal or fabricated percentage.
        self._activity_started = monotonic()
        self._activity_phase = 'Préparation'
        self._activity_files = None
        self._activity_mode = 'preparing'
        self._show_activity()
        timeout = getattr(self.GLib, 'timeout_add_seconds', None)
        if timeout is not None:
            self._activity_clock = timeout(
                1, lambda: self._tick_activity(generation),
            )

        def read():
            try:
                return open_local_inspection(
                    self.parent.service, identity_id=identity.identity_id,
                    control=control, on_mode=lambda mode: self._mode(generation, mode),
                )
            except Exception:
                raise RuntimeError('Inspection locale indisponible.') from None

        def completed(session):
            if generation != self._generation or control.cancelled() or not self.alive():
                if isinstance(session, LocalInspection):
                    session.close()
                return
            self.control = None
            self._hide_activity()
            if not isinstance(session, LocalInspection):
                self._set_activity_state(
                    'Inspection impossible · aucune absence de livre établie.'
                )
                self.parent.show_error(_inspection_failure_message(session), 'Inspection locale')
                return
            if not session.valid():
                self._set_activity_state(
                    'Inspection interrompue : périmètre ou identité modifiés.'
                )
                # Unlike a failed snapshot envelope, an established private
                # session can really be invalidated by the active scope guard.
                self.parent.show_error(
                    _INSPECTION_FAILURE_MESSAGES['reconciliation_scope_changed'],
                    'Inspection locale',
                )
                return
            self.session = session
            # Persist actual provenance after a fast cache hit: the transient
            # progress line may close before its idle notification is drawn.
            origin = ('cache privé réutilisé'
                      if session.from_audit_cache()
                      else 'nouvelle inspection locale')
            self._set_activity_state(
                ('Inspection terminée · ' if session.summary().get('status') == 'complete'
                 else 'Inspection partielle · preuves non exhaustives · ')
                + origin + '.'
            )
            self.present()

        def failed(_error):
            if generation == self._generation and self.alive():
                self.clear()
                self._set_activity_state(
                    'Inspection impossible · aucune absence de livre établie.'
                )
                self.parent.show_error('Inspection locale indisponible. Relancez la réconciliation.', 'Inspection locale')

        self.parent.runner.submit('Inspection locale de bibliothèque', read, completed, failed,
                                  priority=TaskPriority.MAINTENANCE, kind='maintenance-inspection')

    def present(self):
        Gtk, Adw, Pango = self.Gtk, self.Adw, self.Pango
        session = self.session
        window = Adw.Window(transient_for=self.parent, modal=True, title='Réconciliation · cas locaux')
        window.set_default_size(920, 620)
        # Adw.Window does not add an application header by itself: without
        # one, a private comparison window has no discoverable maximize/close
        # controls on client-side decorated desktops. Always keep it resizable.
        window.set_resizable(True)
        window.set_decorated(True)
        toolbar_view = Adw.ToolbarView()
        heading = Adw.HeaderBar()
        heading.set_title_widget(Gtk.Label(label='Réconciliation · cas locaux'))
        grow = Gtk.Button(label='Agrandir / Restaurer')
        grow.set_tooltip_text(
            'Agrandir la comparaison au maximum de l’écran ou revenir à la taille normale'
        )
        grow.connect('clicked', lambda *_: _toggle_inspection_maximize(window))
        heading.pack_end(grow)
        toolbar_view.add_top_bar(heading)
        self.dialog = window
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for edge in ('top', 'bottom', 'start', 'end'):
            getattr(box, 'set_margin_'+edge)(12)
        notice = Gtk.Label(label='Identité active · détails privés · placement possible uniquement après vérification et confirmation explicite · aucun téléchargement', xalign=0)
        notice.set_wrap(True)
        box.append(notice)
        # This is a private provenance hint only, not a declaration that the
        # filesystem is still unchanged or a right/file link is proven.
        if session.from_audit_cache():
            origin = (
                'Source de l’inspection : résultats privés réutilisés du dernier '
                'audit de réconciliation (cache à usage unique, portée revérifiée).'
            )
        else:
            origin = (
                'Source de l’inspection : nouvelle lecture locale des preuves.'
            )
        origin_note = Gtk.Label(label=origin, xalign=0)
        origin_note.set_wrap(True)
        box.append(origin_note)
        summary = session.summary()
        if summary.get('status') != 'complete' or summary.get('inspection_limited'):
            warning = Gtk.Label(label='Analyse ou affichage incomplet : certaines preuves restent non vérifiées. Un livre non vérifié n’est pas réputé absent.', xalign=0)
            warning.set_wrap(True)
            box.append(warning)
        # These are counts of different evidence objects, not a 1:1 matching
        # between owned rights and local files. States can overlap.
        overview = session.overview()
        synopsis = Gtk.Label(xalign=0, label=(
            f"FICHIERS LOCAUX · {overview.get('files', 0)} cas visibles : "
            f"{overview.get('files_attested', 0)} avec lien attesté · "
            f"{overview.get('files_without_attested_link', 0)} sans lien attesté · "
            f"{overview.get('files_individually_unverified', 0)} non vérifiables"
            f" (partition exclusive)\n"
            f"DROITS ACTIFS · {overview.get('rights_active', 0)} droits du snapshot : "
            f"{overview.get('rights_attested', 0)} reliés par preuve · "
            f"{overview.get('rights_unverified_total', 0)} sans fichier vérifié"
            f" (n'implique pas l'absence)\n"
            + (
                f"PRÊTS PARIS NON ACTIFS · {overview.get('paris_inactive_loans', 0)} "
                "prêt(s) rendus / expirés / inactifs enregistrés, "
                "distincts des droits actifs et consultables seulement.\n"
                if overview.get('paris_inactive_loans', 0) else ""
            )
            + f"PLACEMENTS SQLITE · {overview.get('placements_registered', 0)} enregistrés "
            f"pour l'identité · "
            f"{overview.get('paths_active_missing', 0)} chemin(s) absents encore marqués present "
            f"(à investiguer) · "
            f"{overview.get('paths_historical_missing', 0)} ancien(s) emplacement(s) "
            f"déjà marqués missing (historique, pas anomalie active) · "
            f"{overview.get('paths_other_missing', 0)} chemin(s) absents d'autre état · "
            f"{overview.get('paths', 0)} cas de chemin consultables au total.\n"
            f"Indicateurs non additionnables : "
            f"{overview.get('unattributed_files', 0)} fichiers sans provenance attestée · "
            f"{overview.get('local_without_right', 0)} fichiers sans droit attesté."
        ))
        synopsis.set_wrap(True)
        box.append(synopsis)
        roots = overview.get('roots', ())
        unavailable = overview.get('unavailable_directories', ())
        unavailable_total = overview.get('unavailable_directories_total', len(unavailable))
        notes = overview.get('inventory_limit_notes', ())
        scope_summary = Gtk.Label(xalign=0, label=(
            f"Périmètre local · {len(roots)} racine(s) configurée(s) "
            f"(pas nécessairement parcourues) · "
            f"{unavailable_total} dossier(s) inaccessible(s) · "
            f"{len(notes)} cause(s) de limite d’inventaire"
        ))
        scope_summary.set_wrap(True)
        box.append(scope_summary)
        # This disclosure contains private local filesystem paths. It is
        # attached only to the revocable inspection dialog, not exported to
        # logs, broker, plugins, or the system status page.
        scope = Gtk.Expander(label='Voir les racines et exclusions (privé)')
        coverage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        lines = ['Racines configurées pour inspection (pas une preuve de parcours complet) :']
        lines.extend('• ' + root for root in roots)
        if not roots:
            lines.append('• Aucune racine configurée dans cette vue.')
        if notes:
            lines.append('Limites constatées :')
            lines.extend('• ' + reason for reason in notes)
        if unavailable:
            lines.append('Répertoires inaccessibles ou non vérifiés :')
            lines.extend('• ' + path for path in unavailable)
            if unavailable_total > len(unavailable):
                lines.append(
                    f"• {unavailable_total-len(unavailable)} autre(s) "
                    "répertoire(s) non affiché(s) (limite de 128)."
                )
        if overview.get('skipped_symlinks') or overview.get('skipped_special'):
            lines.append(
                f"Entrées ignorées · liens symboliques : "
                f"{overview.get('skipped_symlinks', 0)} · "
                f"fichiers spéciaux : {overview.get('skipped_special', 0)}"
            )
        lines.append('Aucune absence de livre ne peut être déduite des exclusions.')
        coverage_label = Gtk.Label(label='\n'.join(lines), xalign=0)
        coverage_label.set_wrap(True)
        coverage_label.set_selectable(True)
        coverage.append(coverage_label)
        coverage_scroll = Gtk.ScrolledWindow()
        coverage_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        coverage_scroll.set_min_content_height(96)
        coverage_scroll.set_max_content_height(124)
        coverage_scroll.set_child(coverage)
        scope.set_child(coverage_scroll)
        scope.set_expanded(bool(notes or unavailable))
        box.append(scope)
        legend = Gtk.Label(xalign=0, label=(
            'Droits et fichiers ne se correspondent pas nécessairement un à un. '
            'Un état peut en recouvrir un autre ; aucun total ne prouve la présence '
            'ou l’absence d’un livre.'
        ))
        legend.set_wrap(True)
        box.append(legend)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        provider = Gtk.DropDown.new_from_strings(list(PROVIDERS.values()))
        provider.set_tooltip_text('Filtrer par fournisseur attesté pour les fichiers ; source déclarée pour les droits')
        state = Gtk.DropDown.new_from_strings(list(STATES.values()))
        state.set_tooltip_text('Filtrer par état de preuve')
        search = Gtk.SearchEntry()
        search.set_placeholder_text('Filtrer par titre, auteur, nom ou identifiant…')
        search.set_hexpand(True)
        toolbar.append(provider)
        toolbar.append(state)
        box.append(toolbar)
        box.append(search)
        shortcuts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        show_files = Gtk.Button(label='Fichiers sans provenance attestée')
        show_rights = Gtk.Button(label='Droits non vérifiés')
        shortcuts.append(show_files)
        shortcuts.append(show_rights)
        box.append(shortcuts)
        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        # Allow the main viewport to reach every comparison action even when
        # a long candidate column exceeds the available window height.
        paned.set_vexpand(False)
        listing = Gtk.ListBox()
        listing.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_width(260)
        scroll.set_min_content_height(160)
        scroll.set_child(listing)
        paned.set_start_child(scroll)
        detail = Gtk.Label(label='Sélectionnez un cas pour consulter ses preuves.', xalign=0, yalign=0)
        detail.set_wrap(True)
        detail.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        detail.set_selectable(True)
        detail.set_max_width_chars(72)
        detail_scroll = Gtk.ScrolledWindow()
        detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        detail_scroll.set_min_content_height(160)
        detail_scroll.set_child(detail)
        # Read-only, local side-by-side comparison. This second pane is only
        # present for an inspected right. Its candidate files have *no*
        # attested link to the right; selection never writes or authorizes.
        comparison = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        comparison.set_start_child(detail_scroll)
        candidate_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        candidate_title = Gtk.Label(
            label='Fichiers candidats · comparaison bibliographique uniquement',
            xalign=0,
        )
        candidate_title.set_wrap(True)
        candidate_box.append(candidate_title)
        candidate_note = Gtk.Label(
            label='Même identifiant explicite dans une notice : aucun lien de droit prouvé.',
            xalign=0,
        )
        candidate_note.set_wrap(True)
        candidate_box.append(candidate_note)
        # Manual exploration must be strictly distinct from metadata_only
        # candidates: any unlinked file of this identity can be inspected, but
        # none becomes a suggested right/file match.
        manual_toggle = Gtk.Button(label='Comparer tous les fichiers non liés')
        manual_toggle.set_tooltip_text(
            'Exploration manuelle uniquement : aucune correspondance '
            'bibliographique ni provenance fournisseur supposée.'
        )
        candidate_box.append(manual_toggle)
        manual_search = Gtk.SearchEntry()
        manual_search.set_placeholder_text('Filtrer les fichiers non liés…')
        manual_search.set_visible(False)
        candidate_box.append(manual_search)
        # A real selectable, paginated list replaces the one-file-at-a-time
        # browsing of unattested EPUBs (#2714). A collapsed filter section
        # keeps controls reachable at 920px and can expand in full-screen.
        manual_filters = Gtk.Expander(label='Filtres des fichiers')
        manual_filters.set_tooltip_text(
            'Format EPUB/PDF, SHA calculé, structure/CRC EPUB et ordre de tri'
        )
        filter_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        format_filter = Gtk.DropDown.new_from_strings([
            'Tous les formats', 'EPUB', 'PDF',
        ])
        sha_filter = Gtk.DropDown.new_from_strings([
            'SHA : tous', 'SHA calculé', 'SHA non calculé',
        ])
        crc_filter = Gtk.DropDown.new_from_strings([
            'Structure : toutes', 'EPUB avec CRC validé', 'Autres / non validées',
        ])
        file_sort = Gtk.DropDown.new_from_strings([
            'Tri : titre / auteur', 'Tri : nom de fichier', 'Tri : ordre du scan',
        ])
        for widget in (format_filter, sha_filter, crc_filter, file_sort):
            widget.set_hexpand(True)
            filter_body.append(widget)
        manual_filters.set_child(filter_body)
        manual_filters.set_visible(False)
        candidate_box.append(manual_filters)
        manual_list = Gtk.ListBox()
        manual_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        manual_list_scroll = Gtk.ScrolledWindow()
        manual_list_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        manual_list_scroll.set_min_content_height(90)
        manual_list_scroll.set_max_content_height(180)
        # The list and the comparison details share extra vertical space
        # when the private window is maximized, rather than leaving a fixed
        # one-row widget in the middle of an otherwise empty full-screen pane.
        manual_list_scroll.set_vexpand(True)
        manual_list_scroll.set_child(manual_list)
        manual_list_scroll.set_visible(False)
        candidate_box.append(manual_list_scroll)
        candidate_nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        candidate_prev, candidate_next = (
            Gtk.Button(label='◀ Candidat'),
            Gtk.Button(label='Candidat ▶'),
        )
        candidate_number = Gtk.Label(xalign=0)
        # Keep the status outside the horizontal action row so both navigation
        # buttons stay reachable at 920px modal width / 1280px desktop.
        candidate_number.set_wrap(True)
        candidate_box.append(candidate_number)
        for widget in (candidate_prev, candidate_next):
            candidate_nav.append(widget)
        candidate_box.append(candidate_nav)
        # A right and file can be explicitly compared without permitting an
        # attachment. The separate Core preflight returns *closed reasons*
        # only and never a transaction token or write capability.
        pair_prepare = Gtk.Button(label='Préparer le rattachement')
        pair_prepare.set_tooltip_text(
            'Évaluer en lecture seule les preuves de la paire choisie. '
            'Aucun rattachement ni changement SQLite ne sera effectué.'
        )
        pair_prepare.set_sensitive(False)
        candidate_box.append(pair_prepare)
        fresh_preview = Gtk.Button(label='Vérifier les preuves actuelles…')
        fresh_preview.set_tooltip_text(
            'Revalider le reçu d’achat et le SHA du fichier en tâche de fond. '
            'Prévisualisation uniquement, sans écriture ni rattachement.'
        )
        fresh_preview.set_visible(False)
        candidate_box.append(fresh_preview)
        # The Core-only token never reaches GTK, and a separate explicit
        # consent dialog is required before any placement can be written.
        pair_confirm = Gtk.Button(label='Confirmer le placement local…')
        pair_confirm.set_tooltip_text(
            'Confirmation explicite : une ligne de placement SQLite pourra '
            'être écrite après une nouvelle validation des preuves.'
        )
        pair_confirm.set_visible(False)
        pair_confirm.set_sensitive(False)
        candidate_box.append(pair_confirm)
        # Undo is eligible ONLY for a placement committed in the same still
        # live private session. No journal ID or token is given to GTK.
        pair_undo = Gtk.Button(label='Annuler le dernier placement…')
        pair_undo.set_tooltip_text(
            'Vérifier puis annuler uniquement le placement local créé '
            'pendant cette session, avec une nouvelle confirmation explicite.'
        )
        pair_undo.set_visible(False)
        pair_undo.set_sensitive(False)
        candidate_box.append(pair_undo)
        pair_explanation = Gtk.Label(label='', xalign=0)
        pair_explanation.set_wrap(True)
        pair_explanation.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        pair_explanation.set_selectable(True)
        pair_explanation.set_max_width_chars(45)
        pair_report = Gtk.Expander(label='Préflight local · aucune écriture automatique')
        pair_report.set_child(pair_explanation)
        pair_report.set_visible(False)
        candidate_box.append(pair_report)
        candidate_detail = Gtk.Label(xalign=0, yalign=0)
        candidate_detail.set_wrap(True)
        candidate_detail.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        candidate_detail.set_selectable(True)
        candidate_scroll = Gtk.ScrolledWindow()
        candidate_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        candidate_scroll.set_min_content_height(96)
        candidate_scroll.set_child(candidate_detail)
        candidate_scroll.set_vexpand(True)
        candidate_box.append(candidate_scroll)
        candidate_box.set_visible(False)
        comparison.set_end_child(candidate_box)
        comparison.set_position(315)
        paned.set_end_child(comparison)
        paned.set_position(300)
        box.append(paned)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        previous, following = Gtk.Button(label='Précédent'), Gtk.Button(label='Suivant')
        count = Gtk.Label(xalign=0)
        count.set_hexpand(True)
        close = Gtk.Button(label='Fermer')
        for widget in (previous, count, following, close): footer.append(widget)
        # Keep navigation/close visible while the long inspection moves under
        # the header. Inner lists retain their own scrollbars for large sets.
        toolbar_view.add_bottom_bar(footer)
        content_scroll = Gtk.ScrolledWindow()
        content_scroll.set_name('reconciliation-content-scroll')
        content_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        content_scroll.set_vexpand(True)
        content_scroll.set_propagate_natural_height(False)
        content_scroll.set_child(box)
        toolbar_view.set_content(content_scroll)
        window.set_content(toolbar_view)
        offset = [0]
        current_total = [0]
        candidate_items = [()]
        candidate_index = [0]
        manual_mode = [False]
        manual_index = [0]
        manual_total = [0]
        manual_rebuilding = [False]
        manual_page_size = 25
        selected_right = [None]
        selected_file = [None]
        selected_provider = [None]
        pair_version = [0]
        placement_committed = [False]

        def reset_pair(file_key=None):
            # Invalidate even a still-running SQLite/SHA preview when the
            # user changes files, filters, identity or the inspection window.
            # Never let a late callback reinstate its permission token.
            pair_version[0] += 1
            session.revoke_manual_attachment_preview()
            fresh_preview.set_visible(False)
            fresh_preview.set_sensitive(False)
            pair_confirm.set_visible(False)
            pair_confirm.set_sensitive(False)
            pair_undo.set_visible(False)
            pair_undo.set_sensitive(False)
            selected_file[0] = file_key
            pair_prepare.set_sensitive(
                selected_right[0] is not None and file_key is not None
            )
            pair_report.set_expanded(False)
            pair_report.set_visible(False)
            pair_explanation.set_text('')

        def valid():
            if self.session is session and self.alive() and session.valid():
                return True
            self.clear()
            return False

        def show_candidate():
            if not valid():
                return
            if manual_mode[0]:
                # Closed filters only, selected through a *private* in-memory
                # projection; never query a provider or infer a Numilog right.
                options = {
                    'media_format': ('all', 'epub', 'pdf')[format_filter.get_selected()],
                    'sha_state': ('all', 'calculated', 'not_calculated')[sha_filter.get_selected()],
                    'epub_crc': ('all', 'confirmed', 'other')[crc_filter.get_selected()],
                    'sort': ('title', 'filename', 'scan')[file_sort.get_selected()],
                }
                page_start = manual_index[0] // manual_page_size * manual_page_size
                page = session.unconfirmed_files(
                    offset=page_start, limit=manual_page_size,
                    query=manual_search.get_text()[:160], **options,
                )
                manual_total[0] = page.total
                manual_toggle.set_label('Voir les candidats exacts')
                candidate_title.set_text('Fichiers non liés · comparaison manuelle')
                candidate_note.set_text(
                    'Aucune suggestion fiable n’est nécessaire pour explorer '
                    'les fichiers locaux. Aucun n’est attribué à ce droit '
                    'ni à son fournisseur, même après sélection.'
                )
                candidate_prev.set_label('◀ Fichier')
                candidate_next.set_label('Fichier ▶')
                candidate_number.set_text(
                    f'{manual_index[0]+1 if page.cases else 0} / {page.total}'
                    ' · exploration non probante'
                    + (' · scan incomplet'
                       if session.summary().get('status') != 'complete' else '')
                )
                # Rebuild only one bounded page. The guard prevents
                # programmatic selection from triggering a recursive redraw.
                manual_rebuilding[0] = True
                try:
                    child = manual_list.get_first_child()
                    while child is not None:
                        manual_list.remove(child)
                        child = manual_list.get_first_child()
                    chosen_row = None
                    for position, case in enumerate(page.cases):
                        row = Gtk.ListBoxRow()
                        row._manual_offset = page.offset + position
                        row._manual_file_key = case.key
                        label = Gtk.Label(label=case.label, xalign=0)
                        label.set_wrap(True)
                        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                        label.set_max_width_chars(32)
                        row.set_child(label)
                        manual_list.append(row)
                        if row._manual_offset == manual_index[0]:
                            chosen_row = row
                    if chosen_row is not None:
                        manual_list.select_row(chosen_row)
                finally:
                    manual_rebuilding[0] = False
                current = manual_index[0] - page.offset
                selected = (
                    page.cases[current]
                    if 0 <= current < len(page.cases) else None
                )
                candidate_detail.set_text(
                    selected.label + '\n\n' + selected.details
                    if selected else
                    'Aucun fichier non lié visible avec ce filtre. Cela ne '
                    'prouve pas que le livre soit absent.'
                )
                candidate_prev.set_sensitive(manual_index[0] > 0)
                candidate_next.set_sensitive(
                    manual_index[0] + 1 < page.total
                )
                reset_pair(selected.key if selected is not None else None)
                return
            manual_toggle.set_label('Comparer tous les fichiers non liés')
            candidate_title.set_text(
                'Fichiers candidats · comparaison bibliographique uniquement'
            )
            candidate_note.set_text(
                'Même identifiant explicite dans une notice : '
                'aucun lien de droit prouvé.'
            )
            candidate_prev.set_label('◀ Candidat')
            candidate_next.set_label('Candidat ▶')
            files = candidate_items[0]
            if not files:
                candidate_number.set_text('0 candidat')
                candidate_detail.set_text(
                    'Aucun ISBN ou identifiant bibliographique exact commun '
                    'à une notice de ce droit et aux fichiers non liés dans '
                    'les racines inspectées. Cela ne prouve pas l’absence du livre.'
                )
            else:
                candidate_number.set_text(
                    f'{candidate_index[0]+1} / {len(files)} · non probant'
                )
                candidate = files[candidate_index[0]]
                candidate_detail.set_text(candidate.label + '\n\n' + candidate.details)
            candidate_prev.set_sensitive(bool(files) and candidate_index[0] > 0)
            candidate_next.set_sensitive(
                bool(files) and candidate_index[0]+1 < len(files)
            )
            reset_pair(
                files[candidate_index[0]].key if files else None
            )

        def move_candidate(delta):
            if not valid():
                return
            if manual_mode[0]:
                manual_index[0] = max(0, manual_index[0] + delta)
            else:
                candidate_index[0] = max(
                    0, min(candidate_index[0] + delta, len(candidate_items[0])-1)
                )
            show_candidate()

        def toggle_manual():
            if not valid():
                return
            manual_mode[0] = not manual_mode[0]
            manual_index[0] = 0
            if not manual_mode[0]:
                # A hidden Gtk.ListBox still retains its labels and private
                # filenames in the live widget tree. Leaving this mode must
                # retire that stale page, not just conceal its scroller.
                # Do not reset candidate_index: return to the exact
                # bibliographic candidate previously selected.
                manual_rebuilding[0] = True
                try:
                    child = manual_list.get_first_child()
                    while child is not None:
                        manual_list.remove(child)
                        child = manual_list.get_first_child()
                finally:
                    manual_rebuilding[0] = False
                manual_total[0] = 0
                if manual_search.get_text():
                    manual_search.set_text('')
            manual_search.set_visible(manual_mode[0])
            manual_filters.set_visible(manual_mode[0])
            manual_list_scroll.set_visible(manual_mode[0])
            show_candidate()

        def filter_manual():
            if not valid() or not manual_mode[0]:
                return
            manual_index[0] = 0
            show_candidate()

        def select_manual_file(_listing, row):
            if (manual_rebuilding[0] or not manual_mode[0]
                    or row is None or not valid()):
                return
            # A keyboard/click selection is a local page index, not an
            # acquisition request and never a proof or right/file link.
            # Do NOT destroy Gtk.ListBoxRow widgets from their own
            # row-selected signal: update only the already-present detail.
            manual_index[0] = row._manual_offset
            case = session.detail(row._manual_file_key)
            if case is None:
                return
            candidate_detail.set_text(case.label + '\n\n' + case.details)
            candidate_number.set_text(
                f'{manual_index[0]+1} / {manual_total[0]}'
                ' · exploration non probante'
                + (' · scan incomplet'
                   if session.summary().get('status') != 'complete' else '')
            )
            candidate_prev.set_sensitive(manual_index[0] > 0)
            candidate_next.set_sensitive(manual_index[0] + 1 < manual_total[0])
            reset_pair(case.key)

        def prepare_pair(*_args):
            if (not valid() or selected_right[0] is None
                    or selected_file[0] is None):
                return
            try:
                result = session.preflight_manual_link(
                    selected_right[0], selected_file[0],
                )
                labels = {
                    'blocked': 'Bloqué : preuves contradictoires ou contrôle incomplet.',
                    'insufficient_evidence': 'Preuves insuffisantes pour un rattachement.',
                    'eligible_for_fresh_validation': (
                        'Examen envisageable après revalidation transactionnelle complète.'
                    ),
                    'unavailable': 'Paire ou session indisponible.',
                }
                heading = labels.get(result.status, 'Préflight indisponible.')
                if (result.read_only is not True
                        or result.attachment_authorized is not False):
                    heading = 'Préflight indisponible.'
                    explanation = 'Aucune modification autorisée.'
                else:
                    explanation = result.explanation
                pair_explanation.set_text(
                    heading + '\n' + explanation
                    + '\nAucun fichier n’a été rattaché.'
                )
                fresh_preview.set_visible(
                    result.status == 'eligible_for_fresh_validation'
                    and result.read_only is True
                    and result.attachment_authorized is False
                )
                fresh_preview.set_sensitive(fresh_preview.get_visible())
            except Exception:
                # No raw exception, user metadata or path may cross GTK.
                pair_explanation.set_text(
                    'Préflight local indisponible. Aucune modification autorisée.'
                )
            pair_report.set_visible(True)
            pair_report.set_expanded(True)

        def preview_fresh_pair(*_args):
            if (not valid() or selected_right[0] is None
                    or selected_file[0] is None or not fresh_preview.get_visible()):
                return
            chosen_right, chosen_file = selected_right[0], selected_file[0]
            version = pair_version[0]
            fresh_preview.set_sensitive(False)
            pair_confirm.set_visible(False)
            pair_confirm.set_sensitive(False)
            pair_explanation.set_text(
                'Vérification locale des preuves actuelles… '
                'Aucun rattachement ne sera effectué.'
            )

            def read():
                return session.preview_manual_attachment(
                    chosen_right, chosen_file,
                    cancelled=lambda: (
                        pair_version[0] != version or not session.valid()
                    ),
                )

            def completed(result):
                if (not valid() or version != pair_version[0]
                        or selected_right[0] != chosen_right
                        or selected_file[0] != chosen_file):
                    return
                # Only a closed status, not a private token or exception, is
                # presented to GTK. Confirmation requires a separate UI lot.
                states = {
                    'preview': (
                        'Preuves actuelles cohérentes. La confirmation humaine '
                        'du placement est disponible : le Core vérifiera une '
                        'nouvelle fois compte, reçu et octets juste avant '
                        'l’écriture SQLite. Aucun fichier n’a été rattaché.'
                    ),
                    'already_linked': (
                        'Ce fichier possède déjà un placement explicite vérifié. '
                        'Aucune nouvelle écriture.'
                    ),
                    'blocked': (
                        'Contrôle refusé : preuve, fichier ou périmètre changé '
                        'pendant la vérification. Aucun rattachement.'
                    ),
                    'unavailable': (
                        'Vérification indisponible : relancez une analyse locale. '
                        'Aucun rattachement.'
                    ),
                }
                status = result.get('status') if type(result) is dict else None
                pair_explanation.set_text(states.get(status, states['unavailable']))
                pair_report.set_visible(True)
                pair_report.set_expanded(True)
                pair_confirm.set_visible(status == 'preview')
                pair_confirm.set_sensitive(status == 'preview')
                fresh_preview.set_sensitive(True)

            def failed(_error):
                if (valid() and version == pair_version[0]
                        and selected_right[0] == chosen_right
                        and selected_file[0] == chosen_file):
                    pair_explanation.set_text(
                        'Vérification indisponible. Aucun rattachement.'
                    )
                    fresh_preview.set_sensitive(True)

            self.parent.runner.submit(
                'Vérification locale avant rattachement', read, completed, failed,
                priority=TaskPriority.MAINTENANCE, kind='maintenance-inspection',
            )

        def confirm_local_placement(*_args):
            # GTK never sees a token. The private session consumes its single
            # preview only after the person explicitly confirms this dialog.
            if (not valid() or not pair_confirm.get_visible()
                    or not pair_confirm.get_sensitive()
                    or selected_right[0] is None or selected_file[0] is None):
                return
            chosen_right, chosen_file = selected_right[0], selected_file[0]
            version = pair_version[0]
            pair_confirm.set_sensitive(False)
            fresh_preview.set_sensitive(False)
            pair_prepare.set_sensitive(False)
            # Provider-specific proof, never a shared ISBN or inferred right.
            # The Core will replay the exact transaction under its own lock.
            proof_notice = (
                'Le Core vérifiera à nouveau le droit Kobo personnel, '
                'la sauvegarde antérieure, la révision, le compte et le SHA '
                'du fichier sous verrou avant de restaurer le lien dans SQLite. '
                if selected_provider[0] == 'kobo' else
                'Le Core vérifiera à nouveau la preuve d’achat Numilog et '
                'le fichier EPUB sous verrou avant de créer un seul placement '
                'dans SQLite. '
            )
            dialog = Adw.MessageDialog.new(
                window, 'Confirmer ce placement local ?',
                proof_notice + 'Le livre original est conservé. '
                'Annuler ne modifie rien.',
            )
            dialog.add_response('cancel', 'Annuler')
            dialog.add_response('apply', 'Confirmer le placement')
            dialog.set_default_response('cancel')
            dialog.set_close_response('cancel')
            dialog.set_response_appearance('apply', Adw.ResponseAppearance.DESTRUCTIVE)

            def on_response(_dialog, response):
                if (self.session is not session or not self.alive()
                        or version != pair_version[0]
                        or selected_right[0] != chosen_right
                        or selected_file[0] != chosen_file):
                    return
                if response != 'apply':
                    reset_pair(chosen_file)
                    return
                pair_confirm.set_visible(False)
                pair_explanation.set_text(
                    'Confirmation reçue · vérification transactionnelle en cours…'
                )

                def commit():
                    return session.confirm_manual_attachment(
                        chosen_right, chosen_file, user_confirmed=True,
                        cancelled=lambda: (
                            pair_version[0] != version or not session.valid()
                        ),
                    )

                def completed(outcome):
                    # SQLite may invalidate the old read-only session on a
                    # successful commit. Don't misreport that as a failed write.
                    if (self.session is not session or not self.alive()
                            or version != pair_version[0]
                            or selected_right[0] != chosen_right
                            or selected_file[0] != chosen_file):
                        return
                    status = outcome.get('status') if type(outcome) is dict else None
                    if status == 'applied':
                        # The old observations are NOW stale. Freeze browsing
                        # immediately, but keep exactly this session open so
                        # its private, non-exported undo token remains usable.
                        placement_committed[0] = True
                        for control in (
                            listing, provider, state, search, previous, following,
                            candidate_prev, candidate_next, manual_toggle,
                            manual_search, manual_list, format_filter, sha_filter,
                            crc_filter, file_sort, pair_prepare, fresh_preview,
                            pair_confirm,
                        ):
                            control.set_sensitive(False)
                        pair_confirm.set_visible(False)
                        can_undo = outcome.get('undo_available') is True
                        pair_undo.set_visible(can_undo)
                        pair_undo.set_sensitive(can_undo)
                        pair_explanation.set_text(
                            ('Placement local Kobo enregistré. '
                             if selected_provider[0] == 'kobo' else
                             'Placement local Numilog enregistré. ')
                            + 'Fichier original et droit inchangés. Cette vue '
                            'des preuves est désormais périmée : '
                            'aucune autre sélection possible. '
                            + ('Vous pouvez annuler ce placement une seule '
                               'fois ici, ou fermer puis relancer la '
                               'réconciliation.' if can_undo else
                               'Fermez puis relancez la réconciliation.')
                        )
                        pair_report.set_visible(True)
                        pair_report.set_expanded(True)
                        return
                    if status == 'already_linked':
                        self.clear()
                        notice = Adw.MessageDialog.new(
                            self.parent, 'Réconciliation locale',
                            'Ce fichier possède déjà un placement vérifié. '
                            'Aucune écriture supplémentaire. Relancez la '
                            'réconciliation pour actualiser les preuves.',
                        )
                        notice.add_response('close', 'Fermer')
                        notice.present()
                        return
                    reset_pair(chosen_file)
                    if valid():
                        pair_explanation.set_text(
                            'Placement refusé ou indisponible : preuves ou '
                            'périmètre modifiés. Aucune écriture confirmée.'
                        )
                        pair_report.set_visible(True)
                        pair_report.set_expanded(True)

                def failed(_error):
                    if (self.session is session and self.alive()
                            and version == pair_version[0]
                            and selected_right[0] == chosen_right
                            and selected_file[0] == chosen_file):
                        reset_pair(chosen_file)
                        if valid():
                            pair_explanation.set_text(
                                'Placement indisponible : relancez la '
                                'réconciliation avant un autre essai.'
                            )
                            pair_report.set_visible(True)
                            pair_report.set_expanded(True)

                self.parent.runner.submit(
                    'Confirmation du placement local', commit, completed, failed,
                    priority=TaskPriority.MAINTENANCE, kind='maintenance-inspection',
                )

            dialog.connect('response', on_response)
            dialog.present()

        def undo_local_placement(*_args):
            if (not valid() or placement_committed[0] is not True
                    or not pair_undo.get_visible()
                    or not pair_undo.get_sensitive()
                    or selected_right[0] is None or selected_file[0] is None):
                return
            chosen_right, chosen_file = selected_right[0], selected_file[0]
            version = pair_version[0]
            pair_undo.set_sensitive(False)
            pair_explanation.set_text(
                'Vérification du placement et du reçu avant annulation…'
            )

            def preflight():
                return session.preview_manual_undo(
                    chosen_right, chosen_file,
                    cancelled=lambda: (
                        pair_version[0] != version or not session.valid()
                    ),
                )

            def completed(outcome):
                if (self.session is not session or not self.alive()
                        or version != pair_version[0]
                        or selected_right[0] != chosen_right
                        or selected_file[0] != chosen_file):
                    return
                status = outcome.get('status') if type(outcome) is dict else None
                if status != 'undo_preview':
                    # A stale or invalid undo must never be described as
                    # successful. Ask for a new reconciliation instead.
                    self.clear()
                    notice = Adw.MessageDialog.new(
                        self.parent, 'Annulation non effectuée',
                        'Le placement ne peut pas être annulé avec ces preuves '
                        'actuelles. Aucune suppression confirmée ; relancez '
                        'la réconciliation pour vérifier son état.',
                    )
                    notice.add_response('close', 'Fermer')
                    notice.present()
                    return
                dialog = Adw.MessageDialog.new(
                    window, ('Annuler le placement Kobo ?'
                             if selected_provider[0] == 'kobo'
                             else 'Annuler le placement Numilog ?'),
                    'Seul le dernier placement enregistré pendant cette '
                    'session sera annulé, si ses preuves sont toujours '
                    'cohérentes. Le fichier EPUB et le droit sont conservés. '
                    'Annuler ce dialogue laisse le placement en place.',
                )
                dialog.add_response('cancel', 'Conserver le placement')
                dialog.add_response('undo', 'Confirmer l’annulation')
                dialog.set_default_response('cancel')
                dialog.set_close_response('cancel')
                dialog.set_response_appearance(
                    'undo', Adw.ResponseAppearance.DESTRUCTIVE,
                )

                def on_response(_dialog, response):
                    if (self.session is not session or not self.alive()
                            or version != pair_version[0]
                            or selected_right[0] != chosen_right
                            or selected_file[0] != chosen_file):
                        return
                    if response != 'undo':
                        session.revoke_manual_attachment_preview()
                        pair_undo.set_sensitive(True)
                        pair_explanation.set_text(
                            'Le placement reste enregistré. '
                            'Vous pouvez réexaminer son annulation '
                            'ou fermer puis relancer la réconciliation.'
                        )
                        return
                    pair_explanation.set_text(
                        'Confirmation reçue · annulation sous verrou en cours…'
                    )

                    def commit():
                        return session.confirm_manual_undo(
                            chosen_right, chosen_file, user_confirmed=True,
                            cancelled=lambda: (
                                pair_version[0] != version
                                or not session.valid()
                            ),
                        )

                    def committed(result):
                        if (self.session is not session or not self.alive()
                                or version != pair_version[0]
                                or selected_right[0] != chosen_right
                                or selected_file[0] != chosen_file):
                            return
                        outcome_status = (
                            result.get('status') if type(result) is dict else None
                        )
                        self.clear()
                        text = (
                            'Annulation du placement confirmée. '
                            'Le fichier et le droit sont conservés. '
                            'Relancez la réconciliation pour vérifier '
                            'les preuves actuelles.'
                            if outcome_status in ('undone', 'already_undone') else
                            'Annulation non confirmée. '
                            'Vérifiez les preuves via une nouvelle '
                            'réconciliation avant tout autre essai.'
                        )
                        notification = Adw.MessageDialog.new(
                            self.parent, 'Réconciliation locale', text,
                        )
                        notification.add_response('close', 'Fermer')
                        notification.present()

                    def failed(_error):
                        if (self.session is session and self.alive()
                                and version == pair_version[0]):
                            self.clear()
                            notification = Adw.MessageDialog.new(
                                self.parent, 'Annulation non confirmée',
                                'Relancez la réconciliation pour vérifier '
                                'le placement actuel.',
                            )
                            notification.add_response('close', 'Fermer')
                            notification.present()

                    self.parent.runner.submit(
                        'Confirmation de l’annulation locale',
                        commit, committed, failed,
                        priority=TaskPriority.MAINTENANCE,
                        kind='maintenance-inspection',
                    )

                dialog.connect('response', on_response)
                dialog.present()

            def failed(_error):
                if (self.session is session and self.alive()
                        and version == pair_version[0]):
                    self.clear()
                    notification = Adw.MessageDialog.new(
                        self.parent, 'Annulation indisponible',
                        'Relancez la réconciliation pour vérifier '
                        'le placement actuel.',
                    )
                    notification.add_response('close', 'Fermer')
                    notification.present()

            self.parent.runner.submit(
                'Vérification préalable à l’annulation',
                preflight, completed, failed,
                priority=TaskPriority.MAINTENANCE,
                kind='maintenance-inspection',
            )

        def select(_listing, row):
            if row is None or not valid():
                return
            case = session.detail(row._local_case_key)
            if case is not None:
                states = ', '.join(STATES[name] for name in case.states)
                detail.set_text(PROVIDERS[case.provider]+' · '+states+'\n\n'+case.details)
                selected_right[0] = (
                    case.key if (
                        'rights_without_verified_file' in case.states
                        or (case.provider == 'paris'
                            and 'loan_inactive' in case.states)
                    ) else None
                )
                selected_provider[0] = case.provider if selected_right[0] else None
                pair_prepare.set_label(
                    'Examiner le prêt (lecture seule)' if case.provider == 'paris'
                    else 'Préparer le rattachement'
                )
                reset_pair()
                if case.key.startswith('right:'):
                    candidate_items[0] = session.candidate_files(case.key)
                    candidate_index[0] = 0
                    manual_mode[0] = False
                    manual_index[0] = 0
                    manual_search.set_visible(False)
                    manual_filters.set_visible(False)
                    manual_list_scroll.set_visible(False)
                    if manual_search.get_text():
                        manual_search.set_text('')
                    # An incomplete scan with no metadata_only suggestion
                    # should still offer an immediately searchable local
                    # file list, never imply that the EPUB is absent.
                    manual_mode[0] = not candidate_items[0]
                    manual_search.set_visible(manual_mode[0])
                    manual_filters.set_visible(manual_mode[0])
                    manual_list_scroll.set_visible(manual_mode[0])
                    candidate_box.set_visible(True)
                    show_candidate()
                else:
                    candidate_items[0] = ()
                    candidate_box.set_visible(False)

        def render(*_args, reset=False):
            if not valid():
                return
            if reset: offset[0] = 0
            page = session.page(provider=list(PROVIDERS)[provider.get_selected()],
                                state=list(STATES)[state.get_selected()], offset=offset[0],
                                query=search.get_text()[:160])
            current_total[0] = page.total
            child = listing.get_first_child()
            while child is not None:
                listing.remove(child)
                child = listing.get_first_child()
            detail.set_text('Sélectionnez un cas pour consulter ses preuves.' if page.total else 'Aucun cas dans ces filtres. Ce résultat ne prouve pas l’absence d’un livre.')
            candidate_items[0] = ()
            selected_right[0] = None
            selected_provider[0] = None
            reset_pair()
            candidate_box.set_visible(False)
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
        candidate_prev.connect('clicked', lambda *_: move_candidate(-1))
        candidate_next.connect('clicked', lambda *_: move_candidate(1))
        manual_toggle.connect('clicked', lambda *_: toggle_manual())
        manual_search.connect('search-changed', lambda *_: filter_manual())
        manual_list.connect('row-selected', select_manual_file)
        pair_prepare.connect('clicked', prepare_pair)
        fresh_preview.connect('clicked', preview_fresh_pair)
        pair_confirm.connect('clicked', confirm_local_placement)
        pair_undo.connect('clicked', undo_local_placement)
        for widget in (format_filter, sha_filter, crc_filter, file_sort):
            widget.connect('notify::selected', lambda *_: filter_manual())
        provider.connect('notify::selected', lambda *_: render(reset=True))
        state.connect('notify::selected', lambda *_: render(reset=True))
        search.connect('search-changed', lambda *_: render(reset=True))

        def choose(prov, status):
            if not valid():
                return
            # GTK dropdown callbacks will redraw, always clear the textual
            # query only if explicitly requested by the user.
            provider.set_selected(list(PROVIDERS).index(prov))
            state.set_selected(list(STATES).index(status))
            render(reset=True)

        show_files.connect('clicked', lambda *_: choose(
            'unattributed', 'local_file_without_attested_right'))
        show_rights.connect('clicked', lambda *_: choose(
            'all', 'rights_without_verified_file'))
        previous.connect('clicked', lambda *_: move(-50))
        following.connect('clicked', lambda *_: move(50))
        close.connect('clicked', lambda *_: self.clear())
        window.connect('close-request', lambda *_: (self.clear(), False)[1])
        render()
        if self.dialog is window:
            self._watch = self.GLib.timeout_add_seconds(1, valid)
            window.present()
