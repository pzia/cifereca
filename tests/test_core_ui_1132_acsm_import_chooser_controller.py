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
    assert chooser.filters[0].label == "ACSM (*.acsm)"
    assert chooser.filters[0].patterns == ["*.acsm", "*.ACSM"]
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



class HostileIdentity(str):
    def strip(self, *_args):
        raise AssertionError("untrusted identity strip")

    def __eq__(self, _other):
        raise AssertionError("untrusted identity equality")


class HostileGeneration(int):
    def __eq__(self, _other):
        raise AssertionError("untrusted generation equality")


class HostileResult(dict):
    def get(self, _key, _default=None):
        raise AssertionError("untrusted result get")


@pytest.mark.parametrize("mutation", ["identity", "identity_generation", "account_generation"])
def test_acsm_chooser_never_captures_hostile_scope_scalars(mutation):
    host, Gtk, events, created, submitted, identities = _setup()
    if mutation == "identity":
        host.service.current_identity.identity_id = HostileIdentity("alice")
    elif mutation == "identity_generation":
        host._identity_context_generation = HostileGeneration(4)
    else:
        host._account_context_generation = HostileGeneration(7)
    choose_acsm(host, Gtk=Gtk)
    assert created == [] and submitted == []
    assert events == [("error", ("Choisissez une identité pour importer un ACSM",))]


@pytest.mark.parametrize("mutation", ["identity", "identity_generation", "account_generation"])
def test_admitted_acsm_worker_keeps_pinned_identity_with_hostile_gtk_callback_scope(mutation):
    host, Gtk, events, created, submitted, identities = _setup()
    _submit(host, Gtk, created)
    work, done = submitted[0][0][1:3]
    if mutation == "identity":
        host.service.current_identity.identity_id = HostileIdentity("alice")
    elif mutation == "identity_generation":
        host._identity_context_generation = HostileGeneration(4)
    else:
        host._account_context_generation = HostileGeneration(7)
    assert work() == {"status": "complete"}
    assert events == [("import", Path("/tmp/explicit.acsm"), "alice")]
    done({"status": "complete"})
    assert events == [("import", Path("/tmp/explicit.acsm"), "alice")]


@pytest.mark.parametrize("result", [
    None,
    {},
    {"status": True},
    {"status": "retryable_error", "code": "/PRIVATE/auth?token=SECRET"},
    {"status": HostileIdentity("complete")},
    HostileResult(status="complete"),
])
def test_malformed_acsm_response_cannot_claim_success_or_leak_details(result):
    host, Gtk, events, created, submitted, identities = _setup()
    _submit(host, Gtk, created)
    submitted[0][0][2](result)
    assert events == [(
        "error", ("Résultat de l’import ACSM indisponible.", "Import ACSM impossible"),
    )]
    assert "SECRET" not in repr(events)


@pytest.mark.parametrize("code", [
    "https://PRIVATE.example/?token=SECRET",
    HostileIdentity("adept_profile_required"),
    object(),
])
def test_attention_code_is_closed_public_enum_and_never_untrusted_text(code):
    host, Gtk, events, created, submitted, identities = _setup()
    _submit(host, Gtk, created)
    submitted[0][0][2]({"status": "attention_required", "code": code})
    assert events == [(
        "attention",
        "Import en attente : runtime DRM à configurer\nLe staging est conservé pour une nouvelle tentative.",
        {"code": "attention_required"},
    )]
    assert "SECRET" not in repr(events)


def test_known_adept_attention_code_is_preserved_without_provider_text():
    host, Gtk, events, created, submitted, identities = _setup()
    _submit(host, Gtk, created)
    submitted[0][0][2]({
        "status": "attention_required", "code": "adept_profile_ambiguous",
        "private": "https://SECRET.example",
    })
    assert events == [(
        "attention",
        "Import en attente : adept_profile_ambiguous\nLe staging est conservé pour une nouvelle tentative.",
        {"code": "adept_profile_ambiguous"},
    )]


@pytest.mark.parametrize("path", [
    HostileIdentity("/tmp/explicit.acsm"),
    "/tmp/invalid\x00.acsm",
])
def test_bad_chooser_path_does_not_queue_import(path):
    host, Gtk, events, created, submitted, identities = _setup()
    choose_acsm(host, Gtk=Gtk)
    chooser = created[-1]
    chooser.path = path
    chooser.respond(Gtk.ResponseType.ACCEPT)
    assert chooser.destroyed == 1
    assert submitted == []



def test_replaced_core_service_before_chooser_accept_is_not_import_authority():
    host, Gtk, events, created, submitted, identities = _setup()
    choose_acsm(host, Gtk=Gtk)
    chooser = created[-1]
    chooser.path = "/tmp/private.acsm"
    first = host.service
    # Even an identical identity ID and unchanged GTK generations cannot
    # turn a new Core instance into the old dialog's admitted source.
    host.service = NS(
        current_identity=identities["alice"],
        import_acsm=lambda *_a, **_k: events.append(("wrong-service",)),
    )
    chooser.respond(Gtk.ResponseType.ACCEPT)
    assert first is not host.service
    assert chooser.destroyed == 1
    assert host._acsm_chooser is None
    assert submitted == []
    assert events == []


def test_accepted_chooser_pins_original_core_importer_on_service_replacement():
    host, Gtk, events, created, submitted, identities = _setup()
    _submit(host, Gtk, created)
    assert len(submitted) == 1
    work, done = submitted[0][0][1:3]
    initial = host.service
    host.service = NS(
        current_identity=identities["alice"],
        import_acsm=lambda *_a, **_k: events.append(("wrong-service",)),
    )
    assert work() == {"status": "complete"}
    assert events == [("import", Path("/tmp/explicit.acsm"), "alice")]
    done({"status": "complete"})
    done({"status": "attention_required", "code": "adept_profile_required"})
    assert initial is not host.service
    assert events == [("import", Path("/tmp/explicit.acsm"), "alice")]


def test_accepted_chooser_pins_original_importer_even_if_method_reassigned():
    host, Gtk, events, created, submitted, identities = _setup()
    _submit(host, Gtk, created)
    work = submitted[0][0][1]
    host.service.import_acsm = lambda *_a, **_k: events.append(("retargeted",))
    assert work() == {"status": "complete"}
    assert events == [("import", Path("/tmp/explicit.acsm"), "alice")]


@pytest.mark.parametrize("moment", ["chooser", "completion"])
def test_rebuilt_identity_object_with_same_id_cannot_reuse_gtk_result(moment):
    host, Gtk, events, created, submitted, identities = _setup()
    if moment == "chooser":
        choose_acsm(host, Gtk=Gtk)
        chooser = created[-1]
        chooser.path = "/tmp/explicit.acsm"
        host.service.current_identity = NS(identity_id="alice", label="Alice")
        chooser.respond(Gtk.ResponseType.ACCEPT)
        assert submitted == []
        assert events == []
        assert chooser.destroyed == 1
    else:
        _submit(host, Gtk, created)
        host.service.current_identity = NS(identity_id="alice", label="Alice")
        work, done = submitted[0][0][1:3]
        assert work() == {"status": "complete"}
        done({"status": "complete"})
        assert events == [("import", Path("/tmp/explicit.acsm"), "alice")]


def test_manual_acsm_worker_never_reads_window_service_at_execution():
    owner = ast.parse(OWNER.read_text(encoding="utf-8"))
    controller = next(
        node for node in owner.body
        if isinstance(node, ast.FunctionDef) and node.name == "choose_acsm"
    )
    source = ast.unparse(controller)
    assert "lambda: window.service.import_acsm" not in source
    assert "importer = service.import_acsm" in source
    assert "getattr(window, 'service', None) is service" in source
