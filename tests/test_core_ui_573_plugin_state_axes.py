from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.plugin_view import plugin_summary_counts


ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src/cifereca/gui/plugin_manager_page.py"


def _method(name: str) -> str:
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PluginManagerPage")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_summary_separates_activated_ready_and_attention():
    rows = [
        NS(enabled=True, state="ready"),
        NS(enabled=True, state="blocked"),
        NS(enabled=True, state="degraded"),
        NS(enabled=False, state="disabled"),
        NS(enabled=False, state="error"),
    ]
    assert plugin_summary_counts(rows) == {
        "total": 5,
        "activated": 3,
        "ready": 1,
        "attention": 3,
    }


def test_page_summary_names_all_three_axes():
    source = _method("refresh")
    assert "activé(s)" in source
    assert "prêt(s)" in source
    assert "à examiner" in source
    assert "plugin_summary_counts(statuses)" in source


def test_plugin_card_shows_activation_and_execution_state_separately():
    source = _method("_plugin_row")
    assert "Activation ·" in source
    assert "Activé" in source
    assert "Désactivé" in source
    assert "État ·" in source
    assert "Action requise" in source


def test_plugin_card_does_not_expose_raw_reason_or_technical_identity_line():
    source = _method("_plugin_row")
    assert "status.reason" not in source
    assert "f'{d.id} · {d.type}" not in source
    assert "description = str(d.description or '').strip()" in source


def test_friendly_problem_translates_dependency_unavailable():
    source = _method("_friendly_problem")
    assert "dependency_unavailable" in source
    assert "n’est pas disponible" in source


def test_package_card_keeps_version_as_separate_axis():
    source = _method("_append_package")
    assert "Version : à jour" in source
    assert "Version : ancienne" in source
    assert "Version : plus récente / non standard" in source
