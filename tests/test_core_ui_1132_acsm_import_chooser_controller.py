"""CoreUI P0 #1132: explicit ACSM import remains identity scoped."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.acsm_import_chooser_controller import choose_acsm
from cifereca.task_queue import TaskPriority


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "acsm_import_chooser_controller.py"


class Chooser:
    def __init__(self, **props):
        self.props = props
        self.buttons = ()
        self.filters = []
        self.handlers = {}
        self.path = None
        self.presented = 0
        self.destroyed = 0

    def add_buttons(self, *buttons):
        self.buttons = buttons

    def add_filter(self, filt):
        self.filters.append(filt)

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def present(self):
        self.presented += 1

    def destroy(self):
        self.destroyed += 1

    def get_file(self):
        return NS(get_path=lambda: self.path) if self.path else None

    def respond(self, response):
        self.handlers["response"](self, response)


class FileFilter:
    def __init__(self):
        self.label = None
        self.patterns = []

    def set_name(self, label):
        self.label = label

    def add_pattern(self, value):
        self.patterns.append(value)


def _setup(*, identity="alice"):
    events, created, submitted = [], [], []
    identities = {
        "alice": NS(identity_id="alice", label="Alice"),
        "bob": NS(identity_id="bob", label="Bob"),
    }
    service = NS(
        current_identity=identities.get(identity),
        import_acsm=lambda file, *, identity_id: (
            events.append(("import", file, identity_id))
            or {"status": "complete"}
        ),
    )
    host = NS(
        _closed=False,
        _identity_context_generation=4,
        _account_context_generation=7,
        service=service,
        show_error=lambda *args: events.append(("error", args)),
        show_attention=lambda message, **kwargs: events.append(
            ("attention", message, kwargs)
        ),
        _activity_result=lambda message: events.append(("activity", message)),
        refresh_library=lambda deep: events.append(("library", deep)),
        runner=NS(submit=lambda *args, **kwargs: submitted.append((args, kwargs))),
    )

    def make(**kwargs):
        chooser = Chooser(**kwargs)
        created.append(chooser)
        return chooser

    Gtk = NS(
        FileChooserDialog=make, FileFilter=FileFilter,
        ResponseType=NS(CANCEL=0, ACCEPT=1),
        FileChooserAction=NS(OPEN=2),
    )
    return host, Gtk, events, created, submitted, identities


def _submit(host, Gtk, created, *, path="/tmp/explicit.acsm"):
    choose_acsm(host, Gtk=Gtk)
    chooser = created[-1]
    chooser.path = path
    chooser.respond(Gtk.ResponseType.ACCEPT)
    return chooser


def test_controller_stays_headless_and_facade_is_stable():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(tree)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in item or "task_runner" in item
                   or "main_window" in item for item in imports)
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    facade = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == "choose_acsm")
    assert [arg.arg for arg in facade.args.args] == ["self"]
    assert "acsm_import_chooser_controller.choose_acsm(self, Gtk=Gtk)" in ast.unparse(facade)


def test_no_identity_does_not_create_chooser_or_queue_import():
    host, Gtk, events, created, submitted, identities = _setup(identity=None)
    choose_acsm(host, Gtk=Gtk)
    assert events == [("error", ("Choisissez une identité pour importer un ACSM",))]
    assert created == []
    assert submitted == []


def test_dialog_title_transience_buttons_and_acsm_patterns_are_preserved():
    host, Gtk, events, created, submitted, identities = _setup()
    choose_acsm(host, Gtk=Gtk)
    chooser = created[0]
    assert chooser.props == {
        "title": "Importer un ACSM — Alice",
        "transient_for": host,
        "modal": True,
        "action": Gtk.FileChooserAction.OPEN,
    }
    assert chooser.buttons == ("Annuler", 0, "Importer", 1)
    assert chooser.filters[0].label == "ACSM ou jeton ADEPT téléchargé (*.acsm, *.bin)"
    assert chooser.filters[0].patterns == ["*.acsm", "*.ACSM", "*.bin", "*.BIN"]
    assert chooser.presented == 1
    assert host._acsm_chooser is chooser
    assert submitted == []


def test_repeated_open_presents_one_existing_dialog_without_extra_queue():
    host, Gtk, events, created, submitted, identities = _setup()
    choose_acsm(host, Gtk=Gtk)
    choose_acsm(host, Gtk=Gtk)
    assert len(created) == 1
    assert created[0].presented == 2
    assert submitted == []


def test_cancel_keeps_no_reference_and_does_not_import():
    host, Gtk, events, created, submitted, identities = _setup()
    choose_acsm(host, Gtk=Gtk)
    chooser = created[0]
    chooser.respond(Gtk.ResponseType.CANCEL)
    assert chooser.destroyed == 1
    assert host._acsm_chooser is None
    assert submitted == []


def test_accept_without_file_does_not_submit_work():
    host, Gtk, events, created, submitted, identities = _setup()
    choose_acsm(host, Gtk=Gtk)
    created[0].respond(Gtk.ResponseType.ACCEPT)
    assert created[0].destroyed == 1
    assert host._acsm_chooser is None
    assert submitted == []


def test_explicit_selection_submits_one_task_bound_to_original_identity():
    host, Gtk, events, created, submitted, identities = _setup()
    chooser = _submit(host, Gtk, created)
    assert chooser.destroyed == 1
    assert host._acsm_chooser is None
    assert len(submitted) == 1
    args, kwargs = submitted[0]
    assert args[0] == "Import ACSM…"
    assert kwargs == {"priority": TaskPriority.USER_ACTION, "kind": "acsm-import"}
    assert events == []
    assert args[1]() == {"status": "complete"}
    assert events == [("import", Path("/tmp/explicit.acsm"), "alice")]


@pytest.mark.parametrize("status,label", [
    ("complete", "ACSM importé"),
    ("unchanged", "ACSM déjà importé"),
])
def test_success_callback_refreshes_library_without_implicit_download(status, label):
    host, Gtk, events, created, submitted, identities = _setup()
    _submit(host, Gtk, created)
    submitted[0][0][2]({"status": status})
    assert events == [("activity", label), ("library", False)]


def test_attention_required_preserves_staging_message_and_public_code():
    host, Gtk, events, created, submitted, identities = _setup()
    _submit(host, Gtk, created)
    submitted[0][0][2]({
        "status": "attention_required", "code": "runtime_missing",
    })
    assert events == [(
        "attention",
        "Import en attente : runtime_missing\nLe staging est conservé pour une nouvelle tentative.",
        {"code": "runtime_missing"},
    )]


def test_attention_fallback_code_is_explicit_and_never_claims_success():
    host, Gtk, events, created, submitted, identities = _setup()
    _submit(host, Gtk, created)
    submitted[0][0][2]({"status": "attention_required"})
    assert events == [(
        "attention",
        "Import en attente : runtime DRM à configurer\nLe staging est conservé pour une nouvelle tentative.",
        {"code": "attention_required"},
    )]


@pytest.mark.parametrize("scope", [
    "_closed", "_identity_context_generation", "_account_context_generation",
])
def test_late_chooser_response_cannot_schedule_private_import(scope):
    host, Gtk, events, created, submitted, identities = _setup()
    choose_acsm(host, Gtk=Gtk)
    chooser = created[0]
    chooser.path = "/tmp/private.acsm"
    if scope == "_closed":
        host._closed = True
    else:
        setattr(host, scope, getattr(host, scope) + 1)
    chooser.respond(Gtk.ResponseType.ACCEPT)
    assert chooser.destroyed == 1
    assert host._acsm_chooser is None
    assert submitted == []


def test_identity_pointer_change_without_generation_change_is_still_rejected():
    host, Gtk, events, created, submitted, identities = _setup()
    choose_acsm(host, Gtk=Gtk)
    chooser = created[0]
    chooser.path = "/tmp/old.acsm"
    host.service.current_identity = identities["bob"]
    chooser.respond(Gtk.ResponseType.ACCEPT)
    assert chooser.destroyed == 1
    assert submitted == []


@pytest.mark.parametrize("scope", [
    "_closed", "_identity_context_generation", "_account_context_generation",
])
def test_late_completion_never_repaints_unrelated_identity(scope):
    host, Gtk, events, created, submitted, identities = _setup()
    _submit(host, Gtk, created)
    if scope == "_closed":
        host._closed = True
    else:
        setattr(host, scope, getattr(host, scope) + 1)
    submitted[0][0][2]({"status": "complete"})
    submitted[0][0][2]({"status": "attention_required", "code": "old"})
    assert events == []


def test_replayed_response_does_not_duplicate_import_tasks():
    host, Gtk, events, created, submitted, identities = _setup()
    chooser = _submit(host, Gtk, created)
    chooser.respond(Gtk.ResponseType.ACCEPT)
    assert len(submitted) == 1
    assert chooser.destroyed == 1


def test_stale_dialog_does_not_clear_newer_dialog_reference():
    host, Gtk, events, created, submitted, identities = _setup()
    choose_acsm(host, Gtk=Gtk)
    old = created[0]
    # An external settings rebuild can supersede a native chooser.
    host._acsm_chooser = None
    choose_acsm(host, Gtk=Gtk)
    current = created[1]
    old.path = "/tmp/old.acsm"
    old.respond(Gtk.ResponseType.ACCEPT)
    assert host._acsm_chooser is current
    assert old.destroyed == 1
    assert submitted == []


def test_closed_host_does_not_touch_service_or_detached_chooser():
    choose_acsm(NS(_closed=True), Gtk=None)
