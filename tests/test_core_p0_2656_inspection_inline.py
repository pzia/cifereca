"""P0 #2656 — activity belongs to Maintenance row, not a waiting modal."""
from types import SimpleNamespace as NS

from cifereca.gui.library_inspection_view import LocalInspectionController
from tests.test_library_audit_broker import scoped


class FakeBox:
    def __init__(self, **_kwargs):
        self.children = []
        self.visible = True
    def append(self, child):
        self.children.append(child)
    def set_visible(self, value):
        self.visible = value


class FakeLabel:
    def __init__(self, **kwargs):
        self.text = kwargs.get('label', '')
    def set_label(self, text):
        self.text = text


class FakeButton:
    def __init__(self, **kwargs):
        self.label = kwargs.get('label', '')
        self.handlers = {}
    def set_tooltip_text(self, _text):
        pass
    def connect(self, event, callback):
        self.handlers[event] = callback
    def click(self):
        self.handlers['clicked'](self)


def test_inline_activity_has_no_modal_and_only_closed_progress(scoped):
    service, _, _ = scoped
    jobs, pending, errors, rows = [], [], [], []
    Gtk = NS(Box=FakeBox, Label=FakeLabel, Button=FakeButton,
             Orientation=NS(HORIZONTAL=0))
    Adw = NS(MessageDialog=NS(new=lambda *_args: (_ for _ in ()).throw(
        AssertionError('waiting modal must not exist'))))
    parent = NS(service=service, runner=NS(submit=lambda *args, **kwargs: jobs.append(args)),
                show_error=lambda *args: errors.append(args))
    row = NS(add_suffix=rows.append)
    owner = LocalInspectionController(
        parent=parent, Gtk=Gtk, Adw=Adw, Pango=NS(),
        GLib=NS(idle_add=pending.append, source_remove=lambda _id: None),
        alive=lambda: True,
    )
    owner.attach_activity_row(row)
    owner.start()
    assert owner.dialog is None and owner.session is None
    assert len(rows) == 1 and rows[0].visible
    label, cancel = rows[0].children
    assert label.text == 'Préparation des cas locaux…'
    control = owner.control
    control.report(message='Inventaire local', current=517)
    assert len(pending) == 1
    assert pending.pop(0)() is False
    assert label.text.startswith('Inspection détaillée · préparation : cache valide ou nouvelle lecture · Inventaire local · ')
    assert '517 fichiers observés' in label.text
    assert '00:00 écoulées' in label.text
    control.report(message='/PRIVATE/ACCOUNT/SECRET', current=100, total=100)
    assert not pending and 'PRIVATE' not in label.text

    read, complete = jobs[0][1], jobs[0][2]
    # Actual Core read; no mutation or remote source. A quick cached response
    # and a longer fresh read share exactly the same inline flow.
    session = read()
    assert session is not None
    owner.present = lambda: None
    complete(session)
    assert not rows[0].visible and owner.dialog is None and not errors
    owner.clear()


def test_inline_cancel_blocks_stale_completion_and_progress(scoped):
    service, _, _ = scoped
    jobs, pending, rows, shown = [], [], [], []
    parent = NS(service=service, runner=NS(submit=lambda *args, **kw: jobs.append(args)),
                show_error=lambda *a: shown.append(a))
    owner = LocalInspectionController(
        parent=parent,
        Gtk=NS(Box=FakeBox, Label=FakeLabel, Button=FakeButton,
               Orientation=NS(HORIZONTAL=0)),
        Adw=NS(MessageDialog=NS(new=lambda *_: (_ for _ in ()).throw(
            AssertionError('modal')))),
        Pango=NS(), GLib=NS(idle_add=pending.append, source_remove=lambda _: None),
        alive=lambda: True,
    )
    owner.attach_activity_row(NS(add_suffix=rows.append))
    owner.start()
    generation = owner._generation
    control = owner.control
    control.report(message='Lecture des preuves locales')
    cancel = rows[0].children[-1]
    cancel.click()
    assert owner._generation != generation
    assert control.cancelled() and not rows[0].visible
    for callback in pending:
        assert callback() is False
    assert rows[0].children[0].text == 'Préparation des cas locaux…'
    jobs[0][2](jobs[0][1]())
    assert owner.dialog is None and owner.session is None and not shown


def test_elapsed_clock_and_file_counter_are_bounded_and_revoked(monkeypatch, scoped):
    from cifereca.gui import library_inspection_view as view

    service, _, _ = scoped
    now = [100.0]
    monkeypatch.setattr(view, 'monotonic', lambda: now[0])
    timers, removed, callbacks, jobs, rows = [], [], [], [], []
    fake_glib = NS(
        idle_add=callbacks.append,
        timeout_add_seconds=lambda sec, callback: (
            timers.append((sec, callback)) or 401
        ),
        source_remove=removed.append,
    )
    parent = NS(
        service=service,
        runner=NS(submit=lambda *args, **kwargs: jobs.append(args)),
        show_error=lambda *args: None,
    )
    owner = LocalInspectionController(
        parent=parent, Gtk=NS(Box=FakeBox, Label=FakeLabel,
                              Button=FakeButton, Orientation=NS(HORIZONTAL=0)),
        Adw=NS(), Pango=NS(), GLib=fake_glib, alive=lambda: True,
    )
    owner.attach_activity_row(NS(add_suffix=rows.append))
    owner.start()
    assert timers[0][0] == 1
    label = rows[0].children[0]
    now[0] += 87
    assert timers[0][1]() is True
    assert '01:27 écoulées' in label.text
    assert 'fichiers observés' not in label.text
    owner.control.report(message='Inventaire local', current=143)
    assert callbacks.pop(0)() is False
    assert '143 fichiers observés' in label.text

    owner.control.report(message='Inventaire local', current=11)
    assert callbacks.pop(0)() is False
    assert '143 fichiers observés' in label.text
    for malicious in (True, -12, 999_999_999, 'PRIVATE_BOOK'):
        owner.control.report(message='Inventaire local', current=malicious)
        assert callbacks.pop(0)() is False
        assert '143 fichiers observés' in label.text
        assert 'PRIVATE' not in label.text

    old_tick = timers[0][1]
    owner.clear()
    assert removed == [401]
    assert old_tick() is False
    assert not rows[0].visible
    assert owner._activity_started is None


def test_elapsed_counter_and_phase_reject_private_plugin_prose(monkeypatch, scoped):
    from cifereca.gui import library_inspection_view as view

    service, _, _ = scoped
    now = [25]
    monkeypatch.setattr(view, 'monotonic', lambda: now[0])
    callbacks, jobs, rows = [], [], []
    parent = NS(
        service=service,
        runner=NS(submit=lambda *args, **kw: jobs.append(args)),
        show_error=lambda *_a: None,
    )
    owner = LocalInspectionController(
        parent=parent, Gtk=NS(Box=FakeBox, Label=FakeLabel,
                              Button=FakeButton, Orientation=NS(HORIZONTAL=0)),
        Adw=NS(), Pango=NS(),
        GLib=NS(idle_add=callbacks.append, source_remove=lambda _: None),
        alive=lambda: True,
    )
    owner.attach_activity_row(NS(add_suffix=rows.append))
    owner.start()
    label = rows[0].children[0]
    owner.control.report(message='PRIVATE_HOME/profile.epub', current=321, total=321)
    assert not callbacks
    assert 'PRIVATE' not in label.text
    now[0] += 31
    owner.control.report(
        message='Vérification des chemins enregistrés',
        current=None, total=None,
    )
    assert callbacks.pop(0)() is False
    assert '00:31 écoulées' in label.text
    assert 'fichiers observés' not in label.text
    owner.clear()


def test_real_cache_scan_mode_is_visible_and_stale_mode_cannot_reappear(scoped):
    service, _, _ = scoped
    callbacks, rows, jobs = [], [], []
    parent = NS(
        service=service,
        runner=NS(submit=lambda *args, **kwargs: jobs.append(args)),
        show_error=lambda *_args: None,
    )
    owner = LocalInspectionController(
        parent=parent, Gtk=NS(Box=FakeBox, Label=FakeLabel, Button=FakeButton,
                              Orientation=NS(HORIZONTAL=0)),
        Adw=NS(), Pango=NS(),
        GLib=NS(idle_add=callbacks.append, source_remove=lambda *_a: None),
        alive=lambda: True,
    )
    owner.attach_activity_row(NS(add_suffix=rows.append))
    owner.start()
    generation = owner._generation
    label = rows[0].children[0]
    assert owner._activity_mode == "preparing"
    assert owner._mode(generation, "../../PRIVATE_BOOK") is None
    assert not callbacks
    owner._mode(generation, "new_scan")
    assert len(callbacks) == 1 and callbacks.pop(0)() is False
    assert owner._activity_mode == "new_scan"
    assert "nouveau scan local hors réseau" in label.text
    assert "couverture inconnue" in label.text

    owner._mode(generation, "cached")
    assert callbacks.pop(0)() is False
    assert "preuves réutilisées (cache privé valide)" in label.text
    assert "couverture inconnue" not in label.text
    owner._mode(generation, "new_scan")
    late = callbacks.pop(0)
    owner.clear()
    assert late() is False
    assert owner._activity_mode == "preparing"
    assert not rows[0].visible
    assert "PRIVATE_BOOK" not in label.text


def test_actual_fresh_mode_emitted_before_running_second_scan(scoped):
    from cifereca.core.library_local_inspection import open_local_inspection

    service, _, _ = scoped
    signals = []
    session = open_local_inspection(
        service, identity_id="alice", on_mode=signals.append,
    )
    assert signals == ["new_scan"]
    assert session is not None
    session.close()


def test_independent_inspection_row_tracks_ongoing_then_completed_without_rescan(scoped):
    service, _, _ = scoped
    calls, pending, states, suffixes = [], [], [], []
    inspection = NS(
        add_suffix=suffixes.append, set_subtitle=states.append,
    )
    parent = NS(
        service=service,
        runner=NS(submit=lambda *args, **kw: calls.append(args)),
        show_error=lambda *_a: None,
    )
    owner = LocalInspectionController(
        parent=parent, Gtk=NS(Box=FakeBox, Label=FakeLabel, Button=FakeButton,
                              Orientation=NS(HORIZONTAL=0)),
        Adw=NS(), Pango=NS(),
        GLib=NS(idle_add=pending.append, source_remove=lambda *_a: None),
        alive=lambda: True,
    )
    owner.attach_activity_row(inspection)
    owner.start()
    assert states[-1] == 'Inspection en cours · lecture seule · couverture inconnue.'
    owner._mode(owner._generation, 'new_scan')
    assert pending.pop(0)() is False
    assert states[-1] == 'Nouvelle inspection locale hors réseau · couverture inconnue.'
    read, completed = calls[0][1], calls[0][2]
    inspected = read()
    owner.present = lambda: None
    completed(inspected)
    assert states[-1] == 'Inspection terminée · nouvelle inspection locale.'
    assert not suffixes[0].visible
    old = states[-1]
    owner.clear()  # Closing the private consultation is not an audit failure.
    assert states[-1] == old


def test_independent_inspection_row_preserves_partial_failure_and_cancel(scoped):
    service, _, _ = scoped
    calls, statuses, errors = [], [], []
    owner = LocalInspectionController(
        parent=NS(
            service=service, runner=NS(submit=lambda *args, **kw: calls.append(args)),
            show_error=lambda *args: errors.append(args),
        ),
        Gtk=NS(Box=FakeBox, Label=FakeLabel, Button=FakeButton,
               Orientation=NS(HORIZONTAL=0)),
        Adw=NS(), Pango=NS(),
        GLib=NS(idle_add=lambda _fn: None, source_remove=lambda *_a: None),
        alive=lambda: True,
    )
    owner.attach_activity_row(NS(
        add_suffix=lambda *_a: None, set_subtitle=statuses.append,
    ))
    owner.start()
    assert statuses[-1].startswith('Inspection en cours')
    owner.clear()
    assert statuses[-1] == 'Inspection interrompue · aucun constat d’absence de livre.'
    owner.start()
    calls[-1][2]({
        'schema': 1, 'read_only': True, 'acquisition_authorized': False,
        'repair_authorized': False, 'status': 'incomplete_scan',
        'reasons': ['snapshot_time_budget'],
    })
    assert statuses[-1] == 'Inspection impossible · aucune absence de livre établie.'
    assert len(errors) == 1 and 'SQLite' in errors[0][0]
    owner.clear()


def test_fast_private_cache_hit_remains_explained_after_completion(scoped):
    from cifereca.core.library_audit_broker import LibraryReconciliationReadBrokerImpl
    service, _, _ = scoped
    LibraryReconciliationReadBrokerImpl(service).audit(identity_id="alice")
    jobs, states = [], []
    owner = LocalInspectionController(
        parent=NS(
            service=service, runner=NS(submit=lambda *a, **k: jobs.append(a)),
            show_error=lambda *_a: None,
        ),
        Gtk=NS(Box=FakeBox, Label=FakeLabel, Button=FakeButton,
               Orientation=NS(HORIZONTAL=0)),
        Adw=NS(), Pango=NS(),
        GLib=NS(idle_add=lambda _fn: None, source_remove=lambda *_a: None),
        alive=lambda: True,
    )
    owner.attach_activity_row(NS(
        add_suffix=lambda *_a: None, set_subtitle=states.append,
    ))
    owner.start()
    from_cache = jobs[0][1]()
    assert from_cache.from_audit_cache() is True
    owner.present = lambda: None
    jobs[0][2](from_cache)
    assert states[-1] == 'Inspection terminée · cache privé réutilisé.'
    owner.clear()
    assert states[-1] == 'Inspection terminée · cache privé réutilisé.'
