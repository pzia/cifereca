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
    assert '"Exécuter…"' in source


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
    assert "if selected != 'run':" in source
    assert "service.run(plan, Control())" in source
    assert source.index("service.plan(context)") < source.index("dialog.present()") < source.index("service.run(plan, Control())")


def test_identity_scope_names_active_identity_when_available():
    source = _method("_refresh_tools_page")
    assert "'identity': 'Identité active'" in source
    assert "scope += f' · {identity.label}'" in source
