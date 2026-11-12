"""P0 #3196: inventory scheduling text is not a supplier dispatch."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/sync_controller.py"


def test_inventory_label_is_derived_without_kobo_numilog_branch():
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "inventory_provider"
                for target in node.targets)
    ]
    assert len(assignments) == 1
    expression = assignments[0].value
    assert not any(isinstance(node, ast.IfExp) for node in ast.walk(expression))
    code = compile(ast.fix_missing_locations(ast.Expression(expression)), str(SOURCE), "eval")
    for provider_id, expected in (
        ("kobo", "Kobo"),
        ("numilog", "Numilog"),
        ("synthetic-provider", "Synthetic Provider"),
    ):
        assert eval(code, {"source": NS(provider_id=provider_id)}) == expected


def test_inventory_scheduling_and_consent_guards_are_unchanged():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'if source.provider_id in {"kobo", "numilog"}:' not in source
    assert '"remote_inventory" in inventory_capabilities' in source
    assert 'inventory_reader = getattr(broker, "inventory_refresh_schedule", None)' in source
    assert 'inventory_writer = getattr(broker, "set_inventory_refresh_schedule", None)' in source
    assert '"remote_inventory" in source.sync_capabilities' in source
    assert 'inventory_row.set_title(f"Actualisation automatique des notices {inventory_provider}")' in source
