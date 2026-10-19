"""P0 #1130 — plugin page reload and cache subscription teardown contracts."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.plugin_reload_controller import (
    plugins_changed, subscribe_cached_book_details,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
CONTROLLER = ROOT / "plugin_reload_controller.py"


def _window():
    calls = []
    manager = NS(services=lambda category: (calls.append(("services", category)) or ["cache"]))
    initial, updated = object(), object()

    class Root:
        def remove(self, widget):
            calls.append(("remove", widget))

        def prepend(self, widget):
            calls.append(("prepend", widget))

    window = NS(
        _closed=False,
        plugin_manager=manager,
        service=NS(attach_plugin_manager=lambda plugin: calls.append(("attach", plugin))),
        diagnostics=NS(add=lambda *a, **kw: calls.append(("diagnostic", a, kw))),
        shell_header=initial,
        shell_root=Root(),
        _create_shell_header=lambda: (calls.append(("header",)) or updated),
        _ensure_pages_menu_rightmost=lambda: calls.append(("menu",)),
        _rebuild_plugin_pages=lambda: calls.append(("pages",)),
        _populate_accounts=lambda: calls.append(("accounts",)),
        _subscribe_cached_book_details=lambda: calls.append(("subscribe",)),
        _cached_book_unsubscribe=lambda: calls.append(("unsubscribe",)),
        _cached_book_cache_invalidated=lambda: calls.append(("cache-refresh",)),
    )
    return window, calls, initial, updated


def test_import_boundary_and_mainwindow_facade_signatures():
    tree = ast.parse(CONTROLLER.read_text(encoding="utf-8"))
    names = [ast.unparse(n) for n in ast.walk(tree)
             if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any(n == "import gi" or n.startswith("from gi ") or "main_window" in n
                   for n in names)
    owner = next(n for n in ast.parse(MAIN.read_text(encoding="utf-8")).body
                 if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    methods = {n.name: n for n in owner.body if isinstance(n, ast.FunctionDef)}
    for name, target in (
        ("_plugins_changed", "plugins_changed"),
        ("_subscribe_cached_book_details", "subscribe_cached_book_details"),
    ):
        method = methods[name]
        assert [arg.arg for arg in method.args.args] == ["self"]
        code = ast.unparse(method)
        assert "getattr(self, '_closed', False)" in code
        assert "plugin_reload_controller." + target in code
    facade = ast.unparse(methods["_subscribe_cached_book_details"])
    assert "idle_add=GLib.idle_add" in facade
    assert "subscribe_cached_book_detail_changes=subscribe_cached_book_detail_changes" in facade


def test_live_reload_rebuilds_in_existing_order_and_reuses_plugin_manager():
    window, calls, initial, updated = _window()
    assert plugins_changed(window) is None
    assert calls == [
        ("attach", window.plugin_manager),
        ("remove", initial),
        ("header",),
        ("prepend", updated),
        ("menu",), ("pages",), ("accounts",), ("subscribe",),
    ]
    assert window.shell_header is updated


def test_plugin_toggle_reconnects_providers_only_after_initial_startup():
    window, calls, _initial, _updated = _window()
    window._source_inventory_startup_connection_seen = True
    window._connect_sources_if_open = lambda: calls.append(("reconnect",))
    plugins_changed(window)
    assert calls[-1] == ("reconnect",)
    assert calls.count(("reconnect",)) == 1


def test_closed_or_missing_plugin_manager_skips_all_widget_and_service_access():
    window, calls, _, _ = _window()
    window._closed = True
    assert plugins_changed(window) is None
    assert calls == []
    window._closed = False
    window.plugin_manager = None
    assert plugins_changed(window) is None
    assert calls == []


def test_reload_does_not_touch_shell_when_attach_closes_window():
    window, calls, _, _ = _window()
    def close(_manager):
        calls.append(("attach",))
        window._closed = True
    window.service.attach_plugin_manager = close
    plugins_changed(window)
    assert calls == [("attach",)]


def test_header_creation_that_closes_window_aborts_before_prepend():
    window, calls, initial, updated = _window()
    def close_during_header():
        calls.append(("header",))
        window._closed = True
        return updated
    window._create_shell_header = close_during_header
    plugins_changed(window)
    assert calls == [("attach", window.plugin_manager), ("remove", initial), ("header",)]
    assert window.shell_header is updated


def test_page_rebuild_that_closes_window_skips_accounts_and_cache_subscribe():
    window, calls, _, _ = _window()
    def pages():
        calls.append(("pages",))
        window._closed = True
    window._rebuild_plugin_pages = pages
    plugins_changed(window)
    assert [row[0] for row in calls] == [
        "attach", "remove", "header", "prepend", "menu", "pages",
    ]


def test_service_attachment_failure_does_not_block_shell_recovery():
    window, calls, _, _ = _window()
    def unavailable(_manager):
        raise RuntimeError("plugin provider unavailable")
    window.service.attach_plugin_manager = unavailable
    plugins_changed(window)
    assert calls[0][0] == "diagnostic"
    assert [row[0] for row in calls[1:]] == [
        "remove", "header", "prepend", "menu", "pages", "accounts", "subscribe",
    ]


def test_old_subscription_cancelled_before_new_service_registration_and_idle():
    window, calls, _, _ = _window()
    callbacks, posted = [], []
    def register(services, callback):
        calls.append(("register", services))
        callbacks.append(callback)
        return lambda: calls.append(("dispose-new",))

    subscribe_cached_book_details(
        window, idle_add=lambda callback: posted.append(callback),
        subscribe_cached_book_detail_changes=register,
    )
    assert calls == [
        ("unsubscribe",),
        ("services", "book.detail.enrichment"),
        ("register", ("cache",)),
        ("cache-refresh",),
    ]
    assert callbacks[0]() is None
    assert len(posted) == 1
    assert posted[0]() is None
    assert calls[-1] == ("cache-refresh",)
    window._closed = True
    assert callbacks[0]() is None
    assert len(posted) == 1
    window._cached_book_unsubscribe()
    assert calls[-1] == ("dispose-new",)


def test_subscribe_skips_rebinding_if_unsubscribe_triggers_window_close():
    window, calls, _, _ = _window()
    def unsubscribe():
        calls.append(("unsubscribe",))
        window._closed = True
    window._cached_book_unsubscribe = unsubscribe
    def forbidden(*_args):
        raise AssertionError("should not register after close")
    subscribe_cached_book_details(
        window, idle_add=forbidden, subscribe_cached_book_detail_changes=forbidden,
    )
    assert calls == [("unsubscribe",)]


def test_failed_service_discovery_still_installs_empty_subscription():
    window, calls, _, _ = _window()
    window.plugin_manager.services = lambda category: (_ for _ in ()).throw(
        RuntimeError("service enumeration unavailable")
    )
    observed = []
    subscribe_cached_book_details(
        window, idle_add=lambda fn: None,
        subscribe_cached_book_detail_changes=lambda services, callback: (
            observed.append(tuple(services)) or (lambda: None)
        ),
    )
    assert observed == [()]
    assert window._cached_book_services == ()
    assert calls == [("unsubscribe",), ("cache-refresh",)]
