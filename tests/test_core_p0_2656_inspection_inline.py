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
    assert label.text == 'Inspection · Inventaire local'
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
