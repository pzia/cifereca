"""P0 #1130: one-shot provider scopes and identity-host lifetime, headless."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.gui import identity_view_controller as controller


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "src/cifereca/gui/main_window.py"
OWNER = ROOT / "src/cifereca/gui/identity_view_controller.py"


def _window():
    calls = []
    controls = SimpleNamespace(set_state=lambda state: calls.append(("filters", state)))
    window = SimpleNamespace(
        _closed=False,
        service=SimpleNamespace(
            provider_descriptors=lambda: (
                SimpleNamespace(id="paris"),
                SimpleNamespace(id="kobo"),
            ),
        ),
        search_controls=controls,
        library_controls=controls,
        _activate_identity=lambda identity: calls.append(("identity", identity)),
        navigate=lambda route: calls.append(("navigate", route)),
        run_search=lambda force: calls.append(("search", force)),
        _render_library=lambda: calls.append(("library", None)),
    )
    return window, calls


def test_window_facade_keeps_public_signature_and_delegates():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in klass.body if isinstance(node, ast.FunctionDef) and node.name == "open_identity_view")
    assert [argument.arg for argument in method.args.args] == ["self", "identity_id"]
    assert [argument.arg for argument in method.args.kwonlyargs] == ["route", "providers"]
    assert len(method.body) == 1
    assert "identity_view_controller.open_identity_view" in ast.unparse(method)


def test_owner_imports_no_gtk_gi_or_main_window():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("gi" in entry or "main_window" in entry for entry in imports)


def test_search_uses_one_shot_provider_generator_without_widening_scope():
    window, calls = _window()
    controller.open_identity_view(window, "alice", route="search", providers=(x for x in ("paris", "kobo", "paris")))
    assert calls[0] == ("identity", "alice")
    assert calls[1][0] == "filters"
    assert calls[1][1].providers == ("paris", "kobo")
    assert calls[1][1].scope == "digital"
    assert calls[2:] == [("navigate", "search"), ("search", False)]


def test_library_keeps_existing_scope_and_renders_without_search():
    window, calls = _window()
    controller.open_identity_view(window, "bob", route="library", providers=("local",))
    assert calls[1][1].providers == ("local",)
    assert calls[2:] == [("navigate", "library"), ("library", None)]


def test_empty_sources_preserve_explicit_existing_all_sources_semantics():
    window, calls = _window()
    controller.open_identity_view(window, "bob", route="search")
    assert calls[1][1].providers == ()


@pytest.mark.parametrize("providers", [
    "paris", b"paris", None, 42, ("unknown",), ("paris", ""),
    ("paris", None), (b"paris",), (1,), ("paris", "unknown"),
])
def test_invalid_providers_fail_before_any_identity_or_filter_change(providers):
    window, calls = _window()
    with pytest.raises(ValueError):
        controller.open_identity_view(window, "alice", route="search", providers=providers)
    assert calls == []


@pytest.mark.parametrize("route", ["", "settings", "tools", None])
def test_unknown_route_never_activates_an_identity(route):
    window, calls = _window()
    with pytest.raises(ValueError, match="Vue filtrée inconnue"):
        controller.open_identity_view(window, "alice", route=route, providers=("paris",))
    assert calls == []


def test_provider_list_consulted_once_and_generator_exhausted_exactly_once():
    window, calls = _window()
    passed = []
    def providers():
        for value in ("paris", "kobo"):
            passed.append(value)
            yield value
    controller.open_identity_view(window, "alice", route="search", providers=providers())
    assert passed == ["paris", "kobo"]
    assert calls[1][1].providers == ("paris", "kobo")


def test_closed_window_does_not_mutate_identity_controls_or_route():
    window, calls = _window()
    window._closed = True
    controller.open_identity_view(window, "alice", route="search", providers=("paris",))
    assert calls == []


def test_synchronous_close_during_activation_never_updates_detached_controls():
    window, calls = _window()
    def activate(identity):
        calls.append(("identity", identity))
        window._closed = True
    window._activate_identity = activate
    controller.open_identity_view(window, "alice", route="library", providers=("paris",))
    assert calls == [("identity", "alice")]


def test_synchronous_close_during_route_does_not_render_detached_page():
    window, calls = _window()
    def navigate(route):
        calls.append(("navigate", route))
        window._closed = True
    window.navigate = navigate
    controller.open_identity_view(window, "alice", route="search", providers=("paris",))
    assert [row[0] for row in calls] == ["identity", "filters", "navigate"]
