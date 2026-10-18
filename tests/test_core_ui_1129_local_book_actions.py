"""P0 #1129: local book actions stay identity-scoped after extraction."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.local_book_action_controller import add_local_book_actions


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "local_book_action_controller.py"


class Button:
    def __init__(self, label=None, **kwargs):
        self.label = label
        self.handlers = {}
        self.children = []

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def click(self):
        self.handlers["clicked"](self)

    def add_css_class(self, name):
        pass

    def set_tooltip_text(self, value):
        self.tooltip = value

    def set_popover(self, popover):
        self.popover = popover

    def set_child(self, child):
        self.child = child

    def append(self, child):
        self.children.append(child)

    def set_margin_top(self, value):
        pass

    set_margin_bottom = set_margin_start = set_margin_end = set_margin_top


class Popover:
    def set_child(self, child):
        self.child = child


class RouteRequest:
    def __init__(self, artifact, *, target_id=None, identity_id=None):
        self.artifact = artifact
        self.target_id = target_id
        self.identity_id = identity_id


def _environment(*, item=None, identities=("alice", "bob"), local=True,
                 manager=None, attested=False):
    events = []
    item = item if item is not None else {
        "placement_id": "placement", "identity_id": "alice",
        "item_id": "book-1", "provider_id": "kobo",
        "source_credential": "credential-a",
    }
    actions, submissions, launched = [], [], []
    detail = NS(add_actions=lambda buttons: actions.extend(buttons))
    identity_rows = tuple(NS(identity_id=i, label=i.title()) for i in identities)

    class Service:
        current_identity = NS(identity_id="alice")
        identities = identity_rows

        def __init__(self):
            self.exists = local
            self.owned = set()

        def local_book_path(self, placement, *, identity_id):
            events.append(("path", placement, identity_id))
            if not self.exists:
                raise FileNotFoundError(placement)
            return NS(as_uri=lambda: "file:///library/book.epub")

        def local_placement_for_source(self, *, provider_id, credential_id,
                                       item_id, identity_id):
            events.append(("lookup", identity_id))
            return {"placement_id": "owned"} if identity_id in self.owned else None

        def materialize_to_identity(self, placement, target_id):
            events.append(("send", placement, target_id))
            return "complete"

    service = Service()
    window = NS(
        service=service,
        plugin_manager=manager,
        action_button=lambda label, icon: Button(label),
        _add_catalogue_action=lambda panel, record: events.append(("catalogue",)),
        _book_action_context=lambda record: NS(artifact="art-1"),
        runner=NS(submit=lambda *args, **kwargs: submissions.append((args, kwargs))),
        diagnostics=NS(add=lambda *args, **kwargs: events.append(("diagnostic",))),
        _activity_result=lambda message: events.append(("activity", message)),
        refresh_recent=lambda: events.append(("recent",)),
        refresh_library=lambda fast: events.append(("library", fast)),
        show_error=lambda message, title: events.append(("error", title)),
    )
    Gio = NS(AppInfo=NS(launch_default_for_uri=lambda uri, ctx: launched.append(uri)))
    Gtk = NS(MenuButton=Button, Popover=Popover,
             Box=lambda **kwargs: Button(), Button=Button,
             Orientation=NS(VERTICAL=1))
    dependencies = {
        "Gtk": Gtk, "Gio": Gio, "ArtifactRouteRequest": RouteRequest,
        "TaskPriority": NS(USER_ACTION=7),
        "identity_has_attested_possession": lambda *_a, **_kw: attested,
    }
    return window, item, detail, actions, events, submissions, launched, dependencies


def test_mainwindow_facade_and_headless_owner_have_explicit_dependencies():
    source = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(source)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any(imp == "import gi" or imp.startswith("from gi ")
                   or "main_window" in imp for imp in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    owner = next(node for node in main.body
                 if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    facade = next(node for node in owner.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "_add_local_book_actions")
    assert [arg.arg for arg in facade.args.args] == ["self", "detail", "item"]
    rendered = ast.unparse(facade)
    assert "local_book_action_controller.add_local_book_actions" in rendered
    for dep in ("Gtk", "Gio", "ArtifactRouteRequest", "TaskPriority",
                "identity_has_attested_possession"):
        assert dep + "=" + dep in rendered


def test_absent_file_offers_catalogue_without_open_or_send():
    window, item, detail, actions, events, submitted, _, deps = _environment(local=False)
    add_local_book_actions(window, detail, item, **deps)
    assert events == [("path", "placement", "alice"), ("catalogue",)]
    assert actions == []
    assert submitted == []


def test_unattested_source_does_not_assume_local_possession():
    window, _, detail, actions, events, _, _, deps = _environment(
        item={"title": "Notice sans source"}
    )
    add_local_book_actions(window, detail, {"title": "Notice sans source"}, **deps)
    assert events == [("catalogue",)]
    assert actions == []


def test_open_rechecks_identity_scoped_path_at_activation_and_handles_failure():
    window, item, detail, actions, events, _, launched, deps = _environment(
        identities=("alice",)
    )
    add_local_book_actions(window, detail, item, **deps)
    assert [button._cifereca_action_id for button in actions] == ["open-local"]
    assert events == [("path", "placement", "alice")]
    actions[0].click()
    assert launched == ["file:///library/book.epub"]
    assert events[-1] == ("path", "placement", "alice")
    window.service.exists = False
    actions[0].click()
    assert events[-1] == ("error", "Ouverture du livre impossible")
    assert launched == ["file:///library/book.epub"]


def test_attested_possession_hides_redundant_cross_identity_send():
    read = NS(book=lambda book_id: NS(possessions=(object(),)))
    manager = NS(broker=lambda name: read if name == "library.read" else None)
    window, item, detail, actions, _, _, _, deps = _environment(
        manager=manager, attested=True,
    )
    add_local_book_actions(window, detail, item, **deps)
    assert [button._cifereca_action_id for button in actions] == ["open-local"]


def test_existing_placement_suppresses_redundant_send_without_broker():
    window, item, detail, actions, _, _, _, deps = _environment()
    window.service.owned.add("bob")
    add_local_book_actions(window, detail, item, **deps)
    assert [button._cifereca_action_id for button in actions] == ["open-local"]


def test_send_is_deferred_and_rechecks_source_before_materialization():
    window, item, detail, actions, events, submitted, _, deps = _environment()
    add_local_book_actions(window, detail, item, **deps)
    assert [button._cifereca_action_id for button in actions] == [
        "open-local", "send-local:bob",
    ]
    assert not submitted
    actions[1].click()
    assert len(submitted) == 1
    args, kwargs = submitted[0]
    assert args[0] == "Envoi du fichier local…"
    assert kwargs["priority"] == 7
    assert kwargs["kind"] == "materialize"
    assert kwargs["control"] is actions[1]
    assert args[1]() == "complete"
    assert events[-2:] == [("path", "placement", "alice"),
                            ("send", "placement", "bob")]
    args[2]("complete")
    assert events[-3:] == [("activity", "Livre envoyé"),
                            ("recent",), ("library", False)]


def test_routing_uses_selected_artifact_target_and_identity_without_guesswork():
    operations = []
    routing = NS(
        targets=lambda request: (
            operations.append(("targets", request.artifact, request.identity_id))
            or [NS(target=NS(label="Cloud", target_id="cloud-1"))]
        ),
        plan=lambda request: (operations.append(("plan", request.target_id))
                              or "transfer-plan"),
        transfer=lambda plan: (operations.append(("transfer", plan))
                               or "complete"),
    )
    manager = NS(broker=lambda name: routing if name == "artifact.routing" else None)
    window, item, detail, actions, events, submitted, _, deps = _environment(manager=manager)
    add_local_book_actions(window, detail, item, **deps)
    assert [a._cifereca_action_id for a in actions] == [
        "open-local", "send-local:bob", "artifact-routing",
    ]
    assert operations == [("targets", "art-1", "alice")]
    routing_button = actions[2].popover.child.children[0]
    routing_button.click()
    assert len(submitted) == 1
    args, opts = submitted[0]
    assert opts["kind"] == "artifact-route"
    assert opts["priority"] == 7
    assert args[1]() == "complete"
    assert operations[-2:] == [
        ("plan", "cloud-1"), ("transfer", "transfer-plan"),
    ]
    args[2]("complete")
    assert events[-3:] == [("activity", "Livre envoyé"),
                            ("library", False), ("recent",)]


def test_closed_window_is_inert_and_pending_actions_cannot_run_after_close():
    window, item, detail, actions, events, submitted, launched, deps = _environment()
    window._closed = True
    add_local_book_actions(window, detail, item, **deps)
    assert events == []
    assert actions == []

    window._closed = False
    add_local_book_actions(window, detail, item, **deps)
    assert len(actions) == 2
    window._closed = True
    before = list(events)
    actions[0].click()
    actions[1].click()
    assert events == before
    assert launched == []
    assert submitted == []


def test_closed_window_suppresses_deferred_artifact_routing():
    routing = NS(targets=lambda request: [NS(target=NS(label="Cloud", target_id="c"))])
    manager = NS(broker=lambda name: routing if name == "artifact.routing" else None)
    window, item, detail, actions, _, submitted, _, deps = _environment(manager=manager)
    add_local_book_actions(window, detail, item, **deps)
    route_button = actions[-1].popover.child.children[0]
    window._closed = True
    route_button.click()
    assert submitted == []

PRIVATE = "https://private.invalid/LOAN/borrow?token=SECRET_LOCAL_ROUTE"


def test_open_local_book_launcher_error_does_not_leak_private_path():
    window, item, detail, actions, events, _submitted, _, deps = _environment(
        identities=("alice",)
    )
    presented = []
    window.show_error = lambda message, title: presented.append((message, title))

    def fail_launch(*_args):
        raise OSError("file:///home/private/" + PRIVATE)

    deps["Gio"].AppInfo.launch_default_for_uri = fail_launch
    add_local_book_actions(window, detail, item, **deps)
    actions[0].click()
    assert presented == [("Ouverture locale indisponible", "Ouverture du livre impossible")]
    assert "SECRET_LOCAL_ROUTE" not in repr(presented)


def test_known_possessions_exception_does_not_escape_to_diagnostics():
    read = NS(book=lambda book_id: (_ for _ in ()).throw(RuntimeError(PRIVATE)))
    manager = NS(broker=lambda name: read if name == "library.read" else None)
    window, item, detail, actions, events, _submitted, _, deps = _environment(
        identities=("alice",), manager=manager,
    )
    recorded = []
    window.diagnostics = NS(add=lambda *args, **kw: recorded.append((args, kw)))
    add_local_book_actions(window, detail, item, **deps)
    assert [a._cifereca_action_id for a in actions] == ["open-local"]
    assert recorded == [
        (("UI", "read known possessions", "error"),
         {"detail": {"code": "known_possessions_unavailable"}}),
    ]
    assert PRIVATE not in repr(recorded)


def test_route_target_enumeration_exception_is_closed_diagnostic():
    routing = NS(targets=lambda *_: (_ for _ in ()).throw(RuntimeError(PRIVATE)))
    manager = NS(broker=lambda name: routing if name == "artifact.routing" else None)
    window, item, detail, actions, events, _submitted, _, deps = _environment(
        identities=("alice",), manager=manager,
    )
    recorded = []
    window.diagnostics = NS(add=lambda *args, **kw: recorded.append((args, kw)))
    add_local_book_actions(window, detail, item, **deps)
    assert [a._cifereca_action_id for a in actions] == ["open-local"]
    assert recorded == [
        (("UI", "artifact routing targets", "error"),
         {"detail": {"code": "artifact_targets_unavailable"}}),
    ]
    assert PRIVATE not in repr(recorded)


def test_failed_artifact_route_preserves_safe_feedback_and_ignores_late_callbacks():
    from cifereca.plugin_api import UserFacingError

    routing = NS(targets=lambda *_: [NS(target=NS(label="Cloud", target_id="cloud-1"))])
    manager = NS(broker=lambda name: routing if name == "artifact.routing" else None)
    window, item, detail, actions, events, submitted, _, deps = _environment(
        identities=("alice",), manager=manager,
    )
    shown = []
    window.show_error = lambda message, title: shown.append((message, title))
    add_local_book_actions(window, detail, item, **deps)
    actions[-1].popover.child.children[0].click()
    assert len(submitted) == 1
    failed = submitted[0][0][3]

    class HostileError(RuntimeError):
        def __str__(self):
            raise AssertionError("Provider exception must not be stringified")

    failed(HostileError(PRIVATE))
    assert shown == [("Envoi vers le stockage indisponible", "Envoi impossible")]

    failed(UserFacingError(
        "Autorisation nécessaire", code="signin_required",
        technical_detail=PRIVATE,
    ))
    assert shown[-1] == ("Autorisation nécessaire", "Envoi impossible")
    assert PRIVATE not in repr(shown)
    window._closed = True
    failed(HostileError(PRIVATE))
    assert len(shown) == 2
