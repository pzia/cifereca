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
    visible = source.index("self.root_stack.set_visible_child_name('main')")
    schedule = source.index("GLib.idle_add(self.connect_configured_sources)")
    assert build < visible < schedule
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
