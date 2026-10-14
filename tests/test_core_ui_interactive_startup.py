from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"
APP = ROOT / "src/cifereca/app.py"

def _class_method(path: Path, class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)

def test_main_window_builds_and_shows_shell_before_provider_connection():
    method = _class_method(MAIN_WINDOW, "MainWindow", "__init__")
    source = ast.unparse(method)
    build = source.index("self.main_shell = self._build_main_shell()")
    populate = source.index("self._populate_accounts()")
    visible = source.index("self.root_stack.set_visible_child_name('main')")
    assert build < populate < visible
    assert "GLib.idle_add(self.connect_configured_sources)" not in source
    assert "startup.shell_interactive" in source

def test_provider_connection_does_not_gate_or_rebuild_shell():
    method = _class_method(MAIN_WINDOW, "MainWindow", "connect_configured_sources")
    source = ast.unparse(method)
    assert "self._build_main_shell()" not in source
    assert "set_visible_child_name('main')" not in source

def test_reconnect_keeps_shell_visible():
    method = _class_method(MAIN_WINDOW, "MainWindow", "reconnect_sources")
    source = ast.unparse(method)
    assert "set_visible_child_name('startup')" not in source
    assert "self.connect_configured_sources()" in source

def test_application_records_window_presented_timing():
    source = APP.read_text()
    assert "TRACE gui window-presented duration_ms=%.1f" in source
    assert 'self.window.ui_perf.record("startup.window_presented", presented_ms)' in source

def test_dashboard_exposes_local_background_connection_state():
    build = ast.unparse(_class_method(MAIN_WINDOW, "MainWindow", "_build_dashboard_page"))
    connect = ast.unparse(_class_method(MAIN_WINDOW, "MainWindow", "connect_configured_sources"))
    assert "Connexion aux sources en arrière-plan…" in build
    assert "dashboard_loading_status" in connect
    assert "La navigation locale reste disponible." in connect

def test_core_open_keeps_plugin_activation_enabled_by_default():
    runtime = (ROOT / "src/cifereca/core/runtime.py").read_text()
    assert "activate_plugins: bool = True" in runtime
    assert "if activate_plugins:" in runtime
    assert "plugins.activate()" in runtime

def test_gui_defers_plugin_activation_until_after_present():
    app = APP.read_text()
    assert "activate_plugins=False" in app
    present = app.index("self.window.present()")
    schedule = app.index("GLib.idle_add(self.window.activate_startup_plugins)")
    assert present < schedule

def test_fallback_pages_remain_available_before_plugin_activation():
    # Validate the actual extracted page owner and its stable MainWindow facade.
    owner = ROOT / "src/cifereca/gui/shell_page_controller.py"
    tree = ast.parse(owner.read_text(encoding="utf-8"))
    method = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "rebuild_plugin_pages")
    source = ast.unparse(method)
    assert "if not contributed:" in source
    assert "contributed = list(fallback)" in source
    facade = ast.unparse(_class_method(MAIN_WINDOW, "MainWindow", "_rebuild_plugin_pages"))
    assert "shell_page_controller.rebuild_plugin_pages(" in facade

def test_titlebar_falls_back_before_plugin_categories_are_ready():
    source = ast.unparse(_class_method(MAIN_WINDOW, "MainWindow", "_create_shell_header"))
    assert "self.plugin_manager.select('ui.titlebar')" in source
    assert "except Exception:" in source
    assert "self._titlebar_service = None" in source

def test_provider_connection_survives_plugin_ui_refresh_failure():
    source = ast.unparse(_class_method(MAIN_WINDOW, "MainWindow", "activate_startup_plugins"))
    activate = source.index("self.plugin_manager.activate()")
    attach = source.index("self.service.attach_plugin_manager(self.plugin_manager)")
    refresh = source.index("self._plugins_changed()")
    finalizer = source.index("finally:")
    # The no-plugin fallback schedules a connection earlier in source order.
    # Check the normal plugin-activation path, whose schedule follows finally.
    connect = source.rindex("GLib.idle_add(self._connect_sources_if_open)")
    assert activate < attach < refresh < finalizer < connect
