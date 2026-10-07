from __future__ import annotations

import ast
import json
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]


def test_active_recommendations_entrypoint_is_flat_and_uses_core_tasks():
    manifest = json.loads((PLUGIN / "cifereca-plugin.json").read_text(encoding="utf-8"))
    assert manifest["plugins"][0]["entrypoint"] == "recommendations:PLUGIN"

    source = (PLUGIN / "recommendations.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "threading" not in imported
    assert 'context.broker("tasks")' in source
    assert "task_broker.submit(" in source
    assert "recommendations_v0" not in source


def test_obsolete_recommendations_layers_are_removed():
    for name in (
        "recommendations_v021.py",
        "recommendations_v022.py",
        "recommendations_v023.py",
        "recommendations_v024.py",
        "recommendation_service_v022.py",
    ):
        assert not (PLUGIN / name).exists()
    assert (PLUGIN / "recommendation_service.py").exists()
