from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/recommendations"


def test_active_recommendations_entrypoint_uses_core_tasks_not_private_threading():
    manifest = json.loads((PLUGIN / "cifereca-plugin.json").read_text(encoding="utf-8"))
    assert manifest["plugins"][0]["entrypoint"] == "recommendations_v024:PLUGIN"

    for name in ("recommendations.py", "recommendations_v022.py", "recommendations_v023.py", "recommendations_v024.py"):
        tree = ast.parse((PLUGIN / name).read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in tree.body
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert "threading" not in imported

    base = (PLUGIN / "recommendations.py").read_text(encoding="utf-8")
    assert 'context.broker("tasks")' in base
    assert "task_broker.submit(" in base


def test_obsolete_recommendations_layers_are_removed():
    assert not (PLUGIN / "recommendations_v021.py").exists()
    assert not (PLUGIN / "recommendation_service.py").exists()


def test_streaming_layer_no_longer_depends_on_v021():
    source = (PLUGIN / "recommendations_v022.py").read_text(encoding="utf-8")
    assert "recommendations_v021" not in source
    assert "class ProgressRecommendationsView" in source
