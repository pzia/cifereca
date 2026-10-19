"""P0 #1129: public notice/catalogue actions preserve Core URL provenance."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.public_catalogue_action_controller import add_catalogue_action


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
OWNER = ROOT / "public_catalogue_action_controller.py"
MAIN = ROOT / "main_window.py"


class Button:
    def __init__(self, label, icon):
        self.label, self.icon = label, icon
        self.handlers = {}

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def set_tooltip_text(self, value):
        self.tooltip = value

    def click(self):
        self.handlers["clicked"](self)


def _harness(target=("notice", "https://books.example/notice/123"), *, rendered=()):
    events, links, buttons = [], [], []
    state = NS(target=target)
    item = {"title": "Livre", "provider_id": "test"}

    def classify(record):
        assert record is item
        events.append(("classify",))
        return state.target

    window = NS(
        _closed=False,
        _identity_context_generation=7,
        _account_context_generation=11,
        service=NS(public_catalogue_target=classify),
        action_button=lambda label, icon: Button(label, icon),
        toast=lambda text: events.append(("toast", text)),
        show_error=lambda text, title: events.append(("error", text, title)),
    )
    detail = NS(
        _rendered_document_urls=set(rendered),
        add_actions=lambda actions: buttons.extend(actions),
    )
    Gio = NS(AppInfo=NS(
        launch_default_for_uri=lambda uri, context: links.append((uri, context))
    ))
    return window, detail, item, state, events, buttons, links, Gio


def test_real_owner_is_gtk_free_and_window_facade_keeps_signature():
    module = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(module)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in entry or "main_window" in entry for entry in imports)
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    main = next(node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    facade = next(node for node in main.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "_add_catalogue_action")
    assert [arg.arg for arg in facade.args.args] == ["self", "detail", "item"]
    source = ast.unparse(facade)
    assert "public_catalogue_action_controller.add_catalogue_action" in source
    assert "Gio=Gio" in source


@pytest.mark.parametrize("kind,expected_label,expected_action,expected_tip", [
    ("notice", "Ouvrir la notice", "open-notice", "Ouvrir la page publique de ce livre"),
    ("catalogue", "Ouvrir le catalogue", "open-catalogue",
     "Ouvrir le catalogue général pour y rechercher ce livre"),
])
def test_button_semantics_match_attested_classification(
    kind, expected_label, expected_action, expected_tip,
):
    window, detail, item, state, events, buttons, links, Gio = _harness(
        (kind, "https://books.example/path")
    )
    add_catalogue_action(window, detail, item, Gio=Gio)
    assert len(buttons) == 1
    assert buttons[0].label == expected_label
    assert buttons[0].icon == "web-browser-symbolic"
    assert buttons[0]._cifereca_action_id == expected_action
    assert buttons[0].tooltip == expected_tip
    assert events == [("classify",)]
    assert links == []


def test_missing_public_target_is_noop():
    window, detail, item, state, events, buttons, links, Gio = _harness(None)
    add_catalogue_action(window, detail, item, Gio=Gio)
    assert events == [("classify",)]
    assert buttons == []


def test_existing_rendered_document_url_is_not_duplicated():
    target = "https://books.example/notices/42"
    window, detail, item, state, events, buttons, links, Gio = _harness(
        ("notice", target), rendered={target}
    )
    add_catalogue_action(window, detail, item, Gio=Gio)
    assert buttons == []


def test_click_revalidates_public_url_before_launching_browser():
    window, detail, item, state, events, buttons, links, Gio = _harness()
    add_catalogue_action(window, detail, item, Gio=Gio)
    state.target = ("notice", "https://books.example/notice/updated")
    buttons[0].click()
    assert links == [("https://books.example/notice/updated", None)]
    assert events == [("classify",), ("classify",)]


@pytest.mark.parametrize("initial,new", [
    ("notice", "catalogue"),
    ("catalogue", "notice"),
])
def test_changed_classification_cannot_disguise_homepage_as_notice(initial, new):
    window, detail, item, state, events, buttons, links, Gio = _harness(
        (initial, "https://books.example/original")
    )
    add_catalogue_action(window, detail, item, Gio=Gio)
    state.target = (new, "https://books.example/other")
    buttons[0].click()
    assert links == []
    assert events[-1] == (
        "toast", "La destination du lien a changé ; actualisez la fiche."
    )


def test_revoked_link_does_not_open_url_or_raise():
    window, detail, item, state, events, buttons, links, Gio = _harness()
    add_catalogue_action(window, detail, item, Gio=Gio)
    state.target = None
    buttons[0].click()
    assert links == []
    assert events == [("classify",), ("classify",)]


def test_closed_window_skips_service_access_and_button_creation():
    window = NS(_closed=True)
    add_catalogue_action(window, NS(), object(), Gio=None)


def test_late_click_after_close_does_not_reclassify_or_launch():
    window, detail, item, state, events, buttons, links, Gio = _harness()
    add_catalogue_action(window, detail, item, Gio=Gio)
    events.clear()
    window._closed = True
    buttons[0].click()
    assert events == []
    assert links == []


@pytest.mark.parametrize("scope", ["_identity_context_generation", "_account_context_generation"])
def test_stale_detail_action_does_not_read_other_account_or_identity(scope):
    window, detail, item, state, events, buttons, links, Gio = _harness()
    add_catalogue_action(window, detail, item, Gio=Gio)
    events.clear()
    setattr(window, scope, getattr(window, scope) + 1)
    buttons[0].click()
    assert events == []
    assert links == []


def test_browser_error_is_reported_using_existing_public_dialog():
    window, detail, item, state, events, buttons, links, Gio = _harness()
    def fail(uri, ctx):
        raise RuntimeError("URL indisponible")
    Gio.AppInfo.launch_default_for_uri = fail
    add_catalogue_action(window, detail, item, Gio=Gio)
    buttons[0].click()
    assert events[-1] == (
        "error", "Ouverture du lien indisponible", "Ouverture de la page publique impossible"
    )


def test_service_classification_error_during_click_is_reported_once():
    window, detail, item, state, events, buttons, links, Gio = _harness()
    add_catalogue_action(window, detail, item, Gio=Gio)
    window.service.public_catalogue_target = lambda record: (
        (_ for _ in ()).throw(ValueError("lien supprimé"))
    )
    buttons[0].click()
    assert events[-1] == (
        "error", "Ouverture du lien indisponible", "Ouverture de la page publique impossible"
    )
    assert links == []


def test_failure_after_window_destroyed_does_not_present_new_dialog():
    window, detail, item, state, events, buttons, links, Gio = _harness()
    add_catalogue_action(window, detail, item, Gio=Gio)
    def fail(uri, ctx):
        window._closed = True
        raise RuntimeError("late failure")
    Gio.AppInfo.launch_default_for_uri = fail
    buttons[0].click()
    assert not any(event[0] == "error" for event in events)


def test_action_never_uses_a_second_provider_link_resolver():
    window, detail, item, state, events, buttons, links, Gio = _harness()
    add_catalogue_action(window, detail, item, Gio=Gio)
    buttons[0].click()
    assert not hasattr(window, "plugin_manager")
    assert not hasattr(window, "runner")
    assert links == [("https://books.example/notice/123", None)]


@pytest.mark.parametrize("failure_origin", ["service", "browser"])
def test_provider_and_launcher_error_cannot_disclose_signed_url(failure_origin):
    window, detail, item, state, events, buttons, links, Gio = _harness()
    add_catalogue_action(window, detail, item, Gio=Gio)
    def fail(*_args):
        raise RuntimeError(
            "https://private.invalid/LOAN/book?auth=PRIVATE_BROWSER_TOKEN"
        )
    if failure_origin == "service":
        window.service.public_catalogue_target = fail
    else:
        Gio.AppInfo.launch_default_for_uri = fail
    buttons[0].click()
    assert events[-1] == (
        "error", "Ouverture du lien indisponible",
        "Ouverture de la page publique impossible",
    )
    assert "PRIVATE_BROWSER_TOKEN" not in repr(events)
    assert "private.invalid" not in repr(events)
    assert links == []


def test_hostile_exception_does_not_require_str_or_repr():
    window, detail, item, state, events, buttons, links, Gio = _harness()
    add_catalogue_action(window, detail, item, Gio=Gio)
    class HostileError(RuntimeError):
        def __str__(self):
            raise AssertionError("Never stringify provider exceptions")
        def __repr__(self):
            raise AssertionError("Never repr provider exceptions")
    Gio.AppInfo.launch_default_for_uri = lambda *_args: (_ for _ in ()).throw(HostileError())
    buttons[0].click()
    assert events[-1] == (
        "error", "Ouverture du lien indisponible",
        "Ouverture de la page publique impossible",
    )


def test_explicit_userfacing_message_keeps_only_safe_public_text():
    from cifereca.plugin_api import UserFacingError
    window, detail, item, state, events, buttons, links, Gio = _harness()
    add_catalogue_action(window, detail, item, Gio=Gio)
    Gio.AppInfo.launch_default_for_uri = lambda *_args: (_ for _ in ()).throw(
        UserFacingError(
            "Cette page publique est indisponible",
            code="external_link_down",
            technical_detail="https://private.invalid/?token=PRIVATE_BROWSER_TOKEN",
        )
    )
    buttons[0].click()
    assert events[-1] == (
        "error", "Cette page publique est indisponible",
        "Ouverture de la page publique impossible",
    )
    assert "PRIVATE_BROWSER_TOKEN" not in repr(events)
