from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"
STARTUP_PAGE = ROOT / "src/cifereca/gui/startup_page.py"


def _method_source(class_name: str, method_name: str) -> str:
    tree = ast.parse(MAIN_WINDOW.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)
    return ast.unparse(method)


def _startup_source() -> str:
    tree = ast.parse(STARTUP_PAGE.read_text(encoding="utf-8"))
    method = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_startup_page"
    )
    return ast.unparse(method)


def test_startup_actions_use_adaptive_bar():
    # Assert on the real widget owner, not an obsolete MainWindow body.
    source = _startup_source()
    assert "actions = AdaptiveActionBar(source=card)" in source
    assert "Connecter les sources" in source
    assert "Configurer les sources" in source
    assert "connect_configured_sources" in source
    assert "_open_prelogin_diagnostics" in source


def test_startup_card_preserves_window_owned_widgets_and_explicit_actions():
    source = _startup_source()
    assert "window.startup_status = Gtk.Label(" in source
    assert "window.startup_connect_button = Gtk.Button(" in source
    assert 'window.startup_connect_button.connect(\'clicked\'' in source
    assert "window.service.provider_descriptors()" in source
    assert "runner.submit" not in source
    assert "GLib.idle_add" not in source


def test_mainwindow_startup_facade_still_delegates_without_import_cycle():
    facade = _method_source("MainWindow", "_build_startup")
    assert "return build_startup_page(self)" in facade
    source = STARTUP_PAGE.read_text(encoding="utf-8")
    assert "from .main_window import" not in source
    assert "from .startup_page import build_startup_page" in MAIN_WINDOW.read_text()


def test_opening_sync_idle_callback_checks_window_liveness_at_dispatch():
    """#1132: closing between provider success and idle dispatch must suppress sync."""
    source = _method_source("MainWindow", "connect_configured_sources")
    tree = ast.parse(source)
    callbacks = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "idle_add"
    ]
    assert callbacks, "provider startup must schedule opening sync via GTK idle"
    for callback in callbacks:
        assert callback.args, "idle callback is missing"
        idle = callback.args[0]
        assert isinstance(idle, ast.Lambda)
        callback_code = compile(ast.Expression(idle), "<gtk-idle>", "eval")
        class Window:
            _closed = True
            def run_opening_sync(self):
                raise AssertionError("sync invoked after window close")
        # The callback closes over 'self' at runtime; evaluate with a closed
        # stand-in, exactly as GLib would invoke it after window disposal.
        fn = eval(callback_code, {"self": Window()})
        assert fn() is False


def test_startup_without_plugin_manager_still_connects_providers():
    """#1132: absence of optional plugins must not strand provider startup."""
    tree = ast.parse(_method_source("MainWindow", "activate_startup_plugins"))
    no_manager = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Attribute)
        and node.test.left.attr == "plugin_manager"
        and any(isinstance(op, ast.Is) for op in node.test.ops)
    )
    body = ast.unparse(ast.Module(body=no_manager.body, type_ignores=[]))
    assert "GLib.idle_add(lambda: self.connect_configured_sources())" in body
    assert "return False" in body


def test_queued_history_chunk_does_not_touch_disposed_widgets():
    """#1132: a queued history idle must bail out after GTK disposal."""
    source = _method_source("MainWindow", "_append_history_chunk")
    assert "self._history_append_scheduled = False" in source
    assert "getattr(self, '_closed', False)" in source
    assert source.index("getattr(self, '_closed', False)") < source.index("self.history_page.append_rows")


def test_history_scroll_signal_ignores_disposed_window():
    """#1132: late adjustment signals must not queue work on a dead window."""
    source = _method_source("MainWindow", "_history_scroll_changed")
    assert "getattr(self, '_closed', False)" in source
    assert source.index("getattr(self, '_closed', False)") < source.index("adjustment.get_upper()")


def test_window_close_clears_timer_handle_even_when_removal_fails():
    """#1132: stale GLib source IDs must not survive teardown failures."""
    import importlib.util
    module_path = ROOT / "src/cifereca/gui/window_close.py"
    spec = importlib.util.spec_from_file_location("test_window_close_1132", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    class Window:
        _closed = False
        _kobo_backup_timer_id = 42
        _diag_refresh = object()
        _cached_book_unsubscribe = lambda self: None
        _cancel_notification_hide = lambda self: None
        _cancel_activity_hide = lambda self: None
        def get_width(self): return 800
        def get_height(self): return 600
    class Closeable:
        def close(self): pass
    class Config:
        def set(self, *_args): pass
        def save(self): pass
    window = Window()
    window.config = Config()
    window.runner = Closeable()
    window.history_cover_resolver = Closeable()
    window.covers = Closeable()
    window.service = Closeable()
    def fail_remove(_source):
        raise RuntimeError("source already removed")
    assert module.close_window_resources(window, source_remove=fail_remove) is False
    assert window._closed is True
    assert window._kobo_backup_timer_id is None
    assert module.close_window_resources(window, source_remove=fail_remove) is False


def test_book_cache_subscription_does_not_schedule_after_close():
    """#1132: callbacks racing unsubscribe cannot queue GTK work."""
    source = _method_source("MainWindow", "_subscribe_cached_book_details")
    assert "None if self._closed else GLib.idle_add(self._cached_book_cache_invalidated)" in source


def test_window_close_drops_cached_book_subscription_references():
    source = (ROOT / "src/cifereca/gui/window_close.py").read_text()
    assert "window._cached_book_unsubscribe = lambda: None" in source
    assert "window._cached_book_services = ()" in source
    assert "window._cached_book_context = None" in source
