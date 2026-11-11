"""#3196: the removed Kobo-global GUI task cannot be reintroduced."""
from __future__ import annotations

import ast
from pathlib import Path


def test_gui_has_no_independent_kobo_global_backup_worker():
    root = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
    controller = ast.parse((root / "sync_controller.py").read_text(encoding="utf-8"))
    facade = ast.parse((root / "main_window.py").read_text(encoding="utf-8"))
    functions = {
        node.name for node in controller.body if isinstance(node, ast.FunctionDef)
    }
    methods = {
        node.name
        for cls in facade.body if isinstance(cls, ast.ClassDef)
        for node in cls.body if isinstance(node, ast.FunctionDef)
    }
    assert "run_kobo_backup" not in functions
    assert "run_kobo_backup" not in methods
    assert "_configure_kobo_backup_timer" not in functions
    assert "_configure_kobo_backup_timer" not in methods
