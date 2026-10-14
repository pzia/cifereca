"""#1130 — standard shell route dispatch, alias compatibility and plugin fallback."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.shell_page_controller import build_standard_page


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "shell_page_controller.py"

ROUTES = (
    "dashboard", "loans", "search", "history", "bookings", "library",
    "recent", "sync", "tools", "settings",
)


def _host(plugin_manager=None):
    calls = []
    widgets = {route: object() for route in ROUTES}
    host = NS(plugin_manager=plugin_manager)
    for route, widget in widgets.items():
        setattr(host, "_build_" + route + "_page",
                lambda route=route: calls.append(("build", route)) or widgets[route])
    host._plugins_changed = lambda: calls.append(("refresh",))
    return host, widgets, calls


def _dispatch(host, route, *, types=None):
    class Placeholder:
        def __init__(self, **kwargs):
            self.label = kwargs["label"]
    Gtk = NS(Label=Placeholder)
    PluginManagerPage = types or (
        lambda window, manager, on_change: NS(
            window=window, manager=manager, on_change=on_change
        )
    )
    return build_standard_page(
        host, route, Gtk=Gtk, PluginManagerPage=PluginManagerPage
    )


def test_new_owner_is_gtk_free_and_old_mainwindow_signature_is_preserved():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(tree)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in x or "main_window" in x for x in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in main.body
               if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    facade = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_build_standard_page")
    assert [arg.arg for arg in facade.args.args] == ["self", "route"]
    text = ast.unparse(facade)
    assert "shell_page_controller.build_standard_page" in text
    assert "Gtk=Gtk" in text and "PluginManagerPage=PluginManagerPage" in text


@pytest.mark.parametrize("route", ROUTES)
def test_each_built_in_route_calls_only_its_builder_and_preserves_widget(route):
    host, widgets, calls = _host()
    actual = _dispatch(host, route)
    assert actual is widgets[route]
    assert calls == [("build", route)]


def test_loans_and_bookings_keep_singular_mainwindow_page_attributes():
    host, widgets, _calls = _host()
    _dispatch(host, "loans")
    _dispatch(host, "bookings")
    assert host.loan_page is widgets["loans"]
    assert host.booking_page is widgets["bookings"]
    assert not hasattr(host, "loans_page")
    assert not hasattr(host, "bookings_page")


def test_library_keeps_kobo_alias_pointing_at_exact_current_widget():
    host, widgets, _calls = _host()
    _dispatch(host, "library")
    assert host.library_page is widgets["library"]
    assert host.kobo_page is host.library_page
    second = object()
    widgets["library"] = second
    _dispatch(host, "library")
    assert host.kobo_page is second
    assert host.library_page is second


def test_non_library_routes_never_steal_kobo_alias():
    host, widgets, _calls = _host()
    _dispatch(host, "library")
    old = host.kobo_page
    _dispatch(host, "recent")
    _dispatch(host, "search")
    assert host.kobo_page is old


def test_unavailable_plugin_manager_displays_exact_existing_fallback():
    host, widgets, calls = _host()
    fallback = _dispatch(host, "plugins")
    assert fallback.label == "Gestionnaire indisponible"
    assert host.plugins_page is fallback
    assert calls == []


def test_plugin_manager_factory_receives_real_window_and_refresh_callback():
    manager = object()
    host, widgets, calls = _host(manager)
    result = _dispatch(host, "plugins")
    assert result.window is host
    assert result.manager is manager
    assert result.on_change is host._plugins_changed
    assert host.plugins_page is result
    assert calls == []


def test_plugin_page_factory_is_not_called_without_plugin_manager():
    host, _widgets, _calls = _host()
    forbidden = lambda *_: (_ for _ in ()).throw(
        AssertionError("plugin manager unavailable")
    )
    fallback = _dispatch(host, "plugins", types=forbidden)
    assert fallback.label == "Gestionnaire indisponible"


def test_unknown_route_is_rejected_without_constructing_or_assigning_widgets():
    host, widgets, calls = _host()
    with pytest.raises(KeyError):
        _dispatch(host, "unknown")
    assert calls == []
    assert not hasattr(host, "unknown_page")


def test_builder_failure_leaves_existing_page_reference_untouched():
    host, widgets, calls = _host()
    existing = object()
    host.settings_page = existing

    def broken():
        calls.append(("broken", "settings"))
        raise RuntimeError("page invalid")

    host._build_settings_page = broken
    with pytest.raises(RuntimeError, match="page invalid"):
        _dispatch(host, "settings")
    assert host.settings_page is existing
    assert calls == [("broken", "settings")]


def test_dispatch_never_invokes_other_page_builders_or_network_service():
    host, widgets, calls = _host()
    host.service = NS(connect=lambda: (_ for _ in ()).throw(
        AssertionError("must not connect providers")
    ))
    _dispatch(host, "dashboard")
    assert calls == [("build", "dashboard")]
