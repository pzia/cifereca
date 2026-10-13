from __future__ import annotations

import ast
from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _method(name: str) -> str:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_dry_run_action_is_named_preview_not_scheduling():
    source = _method("_refresh_tools_page")
    assert "Prévisualiser…" in source
    assert "Planifier…" not in source
    assert "Exécuter…" in source


def test_preview_dialog_names_summary_changes_warnings_and_scope():
    source = _method("_refresh_tools_page")
    for label in ("Résumé :", "Changements prévus :", "Avertissements :", "Portée :"):
        assert label in source
    assert "Aucun changement prévu" in source
    assert "Aucun" in source
    assert "scope_labels" in source


def test_preview_does_not_execute_until_explicit_run_response():
    source = _method("_refresh_tools_page")
    assert "dialog.add_response('run', 'Exécuter')" in source
    assert "if selected != 'run' or self._closed:" in source
    assert "execute_maintenance_tool" in source
    assert "preview_maintenance_tool" in source
    assert source.index("def present_preview(plan):") < source.index("dialog = Adw.MessageDialog.new")
    assert "service.plan(context)" not in source
    assert "kind='maintenance-plan'" in source
    assert source.index("def response(_dialog, selected):") < source.index("execute_maintenance_tool")
    assert source.index("dialog.connect('response', response)") < source.index("dialog.present()")


def test_identity_scope_names_active_identity_when_available():
    source = _method("_refresh_tools_page")
    assert "'identity': 'Identité active'" in source
    assert "scope += f' · {identity.label}'" in source


def test_plan_runs_in_task_runner_not_in_gtk_callback():
    source = _method("_refresh_tools_page")
    assert "self.runner.submit(" in source
    assert "lambda: preview_maintenance_tool(service, context, destructive=destructive)" in source
    assert "present_preview" in source
    assert "lambda: execute_maintenance_tool(" in source
    assert "if self._closed or _button.get_root() is None:" in source


def test_maintenance_preview_and_execution_reject_identity_switch():
    source = _method("_refresh_tools_page")
    assert source.count("L’identité active a changé.") == 2
    assert source.count("context.identity_id") >= 2
    assert source.index("current_identity = self.service.current_identity") < source.index("dialog = Adw.MessageDialog.new")
