from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.plugin_status_labels import (
    activation_label,
    freshness_label,
    runtime_state_label,
    status_needs_attention,
)


PAGE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/plugin_manager_page.py"


def _method(name: str) -> str:
    tree = ast.parse(PAGE.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PluginManagerPage")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
    return ast.unparse(method)


def test_activation_is_distinct_from_runtime_state():
    blocked = NS(enabled=True, state="blocked")
    disabled = NS(enabled=False, state="disabled")
    assert activation_label(blocked) == "Activé"
    assert runtime_state_label(blocked) == "Bloqué"
    assert status_needs_attention(blocked)
    assert activation_label(disabled) == "Désactivé"
    assert runtime_state_label(disabled) == "Disponible"
    assert not status_needs_attention(disabled)


def test_freshness_is_a_separate_human_version_axis():
    assert freshness_label({
        "builtin": False, "freshness": "up_to_date",
        "active": "1.2.3", "expected_version": "1.2.3",
    }) == "Version · À jour · v1.2.3"
    outdated = freshness_label({
        "builtin": False, "freshness": "outdated",
        "active": "1.2.2", "expected_version": "1.2.3",
    })
    assert "Version · Ancienne" in outdated
    assert "attendue v1.2.3" in outdated


def test_plugin_card_shows_activation_state_and_action_required_without_reason_code():
    source = _method("_plugin_row")
    assert "Activation ·" in source
    assert "État ·" in source
    assert "Action requise ·" in source
    assert "status.reason" not in source
    assert "d.id" not in source
    assert "d.type" not in source


def test_summary_distinguishes_activated_ready_and_attention():
    source = _method("refresh")
    assert "activated = sum(" in source
    assert "status.enabled and status.state == 'ready'" in source
    assert "activé(s)" in source
    assert "prêt(s)" in source
    assert "à examiner" in source


def test_human_blocking_explanation_stays_on_card():
    source = _method("_plugin_row")
    assert "self._friendly_problem(status)" in source
    assert "status_needs_attention(status)" in source
