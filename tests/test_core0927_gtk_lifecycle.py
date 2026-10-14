from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_WINDOW = ROOT / "src/cifereca/gui/main_window.py"
TASK_RUNNER = ROOT / "src/cifereca/gui/task_runner.py"


def _method(path: Path, class_name: str, method_name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text())
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )


def _source(path: Path, class_name: str, method_name: str) -> str:
    return ast.unparse(_method(path, class_name, method_name))


def test_dropdown_refresh_preserves_existing_string_list_model():
    owner = MAIN_WINDOW.with_name("shell_identity_selector_controller.py")
    tree = ast.parse(owner.read_text(encoding="utf-8"))
    method = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "replace_dropdown_labels")
    source = ast.unparse(method)

    assert "model = dropdown.get_model()" in source
    assert "isinstance(model, Gtk.StringList)" in source
    assert "model.splice(0, model.get_n_items(), labels)" in source
    assert "dropdown.set_model(Gtk.StringList.new(labels))" in source

    # Replacement is only the fallback for a non-StringList model.
    string_list_branch = source.index("isinstance(model, Gtk.StringList)")
    splice = source.index("model.splice")
    replacement = source.index("dropdown.set_model")
    assert string_list_branch < splice < replacement


def test_task_completion_is_ignored_after_window_close():
    method = _method(TASK_RUNNER, "TaskRunner", "submit")
    source = ast.unparse(method)

    guards = [
        node for node in ast.walk(method)
        if isinstance(node, ast.If)
        and "getattr(self.window, '_closed', False)" in ast.unparse(node.test)
        and any(isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Constant) and stmt.value.value is False for stmt in node.body)
    ]
    assert guards

    finished = source.index("self.window._task_finished")
    guard = source.index("getattr(self.window, '_closed', False)")
    assert guard < finished


def test_detached_control_is_never_reenabled():
    source = _source(TASK_RUNNER, "TaskRunner", "submit")

    guard = "if control is not None and control.get_root() is not None:"
    enable = "control.set_sensitive(True)"
    assert guard in source
    assert enable in source
    assert source.index(guard) < source.index(enable)


def test_gtk_warning_recipe_remains_outside_simulated_contract():
    backlog = (ROOT / "BACKLOG.md").read_text()

    assert "GTK-C08 recette" in backlog
    assert "Origine exacte non reproduite sans GTK" in backlog
    assert "Ne pas déclarer résolu sur les seuls tests simulés" in backlog
