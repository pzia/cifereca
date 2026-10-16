"""#1497/#1498: one Core placement mutation cannot report multiple changed files.

All responses are synthetic; the destructive operation remains Core-owned.
"""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import MaintenancePlan
from cifereca_library_audits import NamingRepairTool, DuplicateCleanupTool
from tests.test_naming_repair_unverified_changed_p1 import Broker, Context, Control


@pytest.mark.parametrize("tool_class,facts", [
    (NamingRepairTool, {"placement_ids": ("PRIVATE_PLACEMENT",)}),
    (DuplicateCleanupTool, {"pairs": (("PRIVATE_DUPLICATE", "PRIVATE_KEEP"),)}),
])
@pytest.mark.parametrize("bad_count", [2, 5, 100, 1_000_000])
def test_impossible_multi_mutation_count_is_quarantined(tool_class, facts, bad_count):
    broker = Broker(bad_count)
    tool = tool_class().activate(Context(broker))
    plan = MaintenancePlan(
        tool_id=tool.info.id, summary="Bearer PRIVATE_PLAN",
        facts=facts,
    )
    control = Control()
    result = tool.run(plan, control)
    assert len(broker.requests) == 1
    assert result.ok is False
    assert result.changed == 0
    assert result.facts["planned"] == 1
    assert result.facts["failures"] == 1
    assert [(e.get("current"), e.get("total")) for e in control.events] == [
        (0, 1), (1, 1),
    ]
    exported = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "progress": control.events,
    })
    for secret in ("PRIVATE", "Bearer", "/home/", "secret.epub"):
        assert secret not in exported


@pytest.mark.parametrize("tool_class,facts", [
    (NamingRepairTool, {"placement_ids": ("PRIVATE_PLACEMENT",)}),
    (DuplicateCleanupTool, {"pairs": (("PRIVATE_DUPLICATE", "PRIVATE_KEEP"),)}),
])
@pytest.mark.parametrize("changed", [0, 1])
def test_valid_single_placement_mutation_count_remains_supported(tool_class, facts, changed):
    broker = Broker(changed)
    tool = tool_class().activate(Context(broker))
    plan = MaintenancePlan(tool_id=tool.info.id, summary="safe", facts=facts)
    result = tool.run(plan, Control())
    assert result.ok
    assert result.changed == changed
    assert result.facts == {"planned": 1, "failures": 0}
