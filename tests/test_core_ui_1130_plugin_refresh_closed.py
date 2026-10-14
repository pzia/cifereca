"""P0 #1130: late plugin callbacks never reconstruct a closed GTK shell."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from cifereca.gui import plugin_reload_controller


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _callback(name, **namespace):
    # Exercise the real implementation moved from MainWindow, not its AST facade.
    if name == "_plugins_changed":
        return plugin_reload_controller.plugins_changed
    if name == "_subscribe_cached_book_details":
        def dispatch(host):
            glib = namespace.get("GLib", NS(
                idle_add=lambda *_args: (_ for _ in ()).throw(
                    AssertionError("unexpected GLib scheduling")
                ),
            ))
            return plugin_reload_controller.subscribe_cached_book_details(
                host, idle_add=glib.idle_add,
                subscribe_cached_book_detail_changes=namespace["subscribe_cached_book_detail_changes"],
            )
        return dispatch
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    func = next(n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    result = {}
    result.update(namespace)
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__",
                             names=[ast.alias(name="annotations")], level=0),
              func], type_ignores=[],
    )), str(MAIN), "exec"), result)
    return result[name]


def test_plugin_refresh_after_close_does_not_access_service_or_detached_widgets():
    refresh = _callback("_plugins_changed")
    host = NS(_closed=True, plugin_manager=object())
    assert refresh(host) is None


def test_closed_window_never_subscribes_to_plugin_cache():
    def forbidden(*_args):
        raise AssertionError("do not call plugin service after teardown")
    subscribe = _callback(
        "_subscribe_cached_book_details",
        subscribe_cached_book_detail_changes=forbidden,
    )
    host = NS(_closed=True, plugin_manager=object())
    assert subscribe(host) is None


def test_live_plugin_refresh_preserves_attach_shell_page_and_cache_order():
    calls = []
    manager = object()
    initial = object()
    updated = object()
    class Shell:
        def remove(self, widget):
            calls.append(("remove", widget))
        def prepend(self, widget):
            calls.append(("prepend", widget))
    host = NS(
        _closed=False,
        plugin_manager=manager,
        service=NS(attach_plugin_manager=lambda m: calls.append(("attach", m))),
        diagnostics=NS(add=lambda *args, **kwargs: calls.append(("diagnostics",))),
        shell_root=Shell(),
        shell_header=initial,
        _create_shell_header=lambda: updated,
        _ensure_pages_menu_rightmost=lambda: calls.append(("menu",)),
        _rebuild_plugin_pages=lambda: calls.append(("pages",)),
        _populate_accounts=lambda: calls.append(("accounts",)),
        _subscribe_cached_book_details=lambda: calls.append(("subscribe",)),
    )
    _callback("_plugins_changed")(host)
    assert calls == [
        ("attach", manager),
        ("remove", initial),
        ("prepend", updated),
        ("menu",), ("pages",), ("accounts",), ("subscribe",),
    ]
    assert host.shell_header is updated


def test_live_subscription_replaces_previous_before_registering_new():
    calls = []
    manager = NS(services=lambda category: (calls.append(("services", category)) or ("cache",)))
    host = NS(
        _closed=False, plugin_manager=manager,
        _cached_book_unsubscribe=lambda: calls.append(("unsub",)),
        _cached_book_cache_invalidated=lambda: calls.append(("refresh",)),
    )
    def connect(services, callback):
        calls.append(("register", tuple(services)))
        return lambda: calls.append(("close-new",))
    subscribe = _callback(
        "_subscribe_cached_book_details",
        subscribe_cached_book_detail_changes=connect,
        GLib=NS(idle_add=lambda callback: callback()),
    )
    subscribe(host)
    assert calls == [
        ("unsub",), ("services", "book.detail.enrichment"),
        ("register", ("cache",)), ("refresh",),
    ]
    host._cached_book_unsubscribe()
    assert calls[-1] == ("close-new",)
