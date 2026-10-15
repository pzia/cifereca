"""#1497/#1498: no 'success' or complete status when discovery missed artifacts.

The preview still identifies attested safe targets; Core revalidates each
mutating request. Public progress must report the *scope* of the scan.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import LibraryMutationResult, MaintenancePlan
from cifereca_library_audits import NamingRepairTool, DuplicateCleanupTool


class Broker:
    def __init__(self):
        self.requests = []

    def plan(self, request):
        self.requests.append(request)
        return request

    def run(self, request, control):
        return LibraryMutationResult(True, changed=1, summary="PRIVATE_FILENAME")


class Context:
    def __init__(self):
        self.broker = Broker()

    def broker_for(self, name):
        assert name == "library.maintenance"
        return self.broker


class Control:
    def __init__(self):
        self.events = []

    def report(self, **event):
        self.events.append(event)

    def check_cancelled(self):
        pass


@pytest.mark.parametrize("kind", ("rename", "duplicate"))
@pytest.mark.parametrize("items", (0, 1))
def test_incomplete_scan_never_reports_complete_success(kind, items):
    ctx, control = Context(), Control()
    if kind == "rename":
        tool = NamingRepairTool().activate(NS(broker=ctx.broker_for))
        facts = {"placement_ids": ("p1",) if items else (), "read_failures": 2}
    else:
        tool = DuplicateCleanupTool().activate(NS(broker=ctx.broker_for))
        facts = {"pairs": (("p1", "p2"),) if items else (), "read_failures": 2}
    plan = MaintenancePlan(
        tool_id=tool.info.id,
        summary="PRIVATE_PLAN_BEARER",
        warnings=("PRIVATE_PLAN_WARNING",),
        facts=facts,
    )
    result = tool.run(plan, control)
    assert not result.ok
    assert result.changed == items
    assert result.facts == {"planned": items, "failures": 0, "read_failures": 2}
    assert len(ctx.broker.requests) == items
    assert any("2 artefact(s) non inspecté(s)" in s for s in result.warnings)
    assert all("PRIVATE" not in str(s) for s in (
        result.summary, result.warnings, result.facts, control.events,
    ))
    if items:
        assert [(x.get("current"), x.get("total")) for x in control.events] == [(0, 1), (1, 1)]
    else:
        assert all("current" not in e and "total" not in e for e in control.events)
    assert "incomplet" in control.events[-1]["message"] or "partiel" in control.events[-1]["message"] or "artefact(s) non inspecté(s)" in control.events[-1]["message"]


@pytest.mark.parametrize("kind", ("rename", "duplicate"))
def test_complete_plan_preserves_existing_result_contract(kind):
    ctx, control = Context(), Control()
    if kind == "rename":
        tool = NamingRepairTool().activate(NS(broker=ctx.broker_for))
        facts = {"placement_ids": ("p1",), "read_failures": 0}
    else:
        tool = DuplicateCleanupTool().activate(NS(broker=ctx.broker_for))
        facts = {"pairs": (("p1", "p2"),), "read_failures": 0}
    plan = MaintenancePlan(tool_id=tool.info.id, summary="PRIVATE", facts=facts)
    result = tool.run(plan, control)
    assert result.ok and result.changed == 1
    assert result.facts == {"planned": 1, "failures": 0}
    assert control.events[-1]["current"] == control.events[-1]["total"] == 1
