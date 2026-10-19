from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"
STARTUP_COORDINATOR = ROOT / "src/cifereca/gui/startup_coordinator.py"
APP = ROOT / "src/cifereca/app.py"

def _class_method(path: Path, class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == method_name)

def _startup_owner_method(name: str) -> ast.FunctionDef:
    tree = ast.parse(STARTUP_COORDINATOR.read_text(encoding="utf-8"))
    return next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == name)


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
    owner = MAIN_WINDOW.with_name("dashboard_page_controller.py")
    tree = ast.parse(owner.read_text(encoding="utf-8"))
    build = ast.unparse(next(node for node in tree.body
                             if isinstance(node, ast.FunctionDef)
                             and node.name == "build_dashboard_page"))
    shell_facade = ast.unparse(_class_method(MAIN_WINDOW, "MainWindow", "_build_dashboard_page"))
    assert "dashboard_page_controller.build_dashboard_page" in shell_facade
    connect = ast.unparse(_startup_owner_method("connect_configured_sources"))
    facade = ast.unparse(_class_method(MAIN_WINDOW, "MainWindow", "connect_configured_sources"))
    assert "Connexion aux sources en arrière-plan…" in build
    assert "dashboard_loading_status" in connect
    assert "La navigation locale reste disponible." in connect
    assert "startup_coordinator.connect_configured_sources(" in facade
    assert "idle_add=GLib.idle_add" in facade

def test_async_connection_refreshes_query_choices_without_rebuilding_shell():
    source = ast.unparse(_startup_owner_method("connect_configured_sources"))
    assert "refresh_query_providers(window)" in source
    assert source.index("window._populate_accounts()") < source.index(
        "refresh_query_providers(window)"
    )
    assert "window._rebuild_plugin_pages()" not in source


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
    owner = MAIN_WINDOW.with_name("shell_header_controller.py")
    tree = ast.parse(owner.read_text(encoding="utf-8"))
    source = ast.unparse(next(node for node in tree.body
                              if isinstance(node, ast.FunctionDef)
                              and node.name == "create_shell_header"))
    facade = ast.unparse(_class_method(MAIN_WINDOW, "MainWindow", "_create_shell_header"))
    assert "shell_header_controller.create_shell_header(" in facade
    assert "window.plugin_manager.select('ui.titlebar')" in source
    assert "except Exception:" in source
    assert "window._titlebar_service = None" in source

def test_provider_connection_survives_plugin_ui_refresh_failure():
    source = ast.unparse(_startup_owner_method("activate_startup_plugins"))
    facade = ast.unparse(_class_method(MAIN_WINDOW, "MainWindow", "activate_startup_plugins"))
    activate = source.index("window.plugin_manager.activate()")
    attach = source.index("window.service.attach_plugin_manager(window.plugin_manager)")
    refresh = source.index("window._plugins_changed()")
    finalizer = source.index("finally:")
    # The no-plugin fallback schedules a connection earlier in source order.
    # Check the normal plugin-activation path, whose schedule follows finally.
    connect = source.rindex("idle_add(window._connect_sources_if_open)")
    assert activate < attach < refresh < finalizer < connect
    assert "startup_coordinator.activate_startup_plugins(" in facade
    assert "idle_add=GLib.idle_add" in facade
