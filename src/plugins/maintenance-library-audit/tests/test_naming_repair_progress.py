"""#1497: naming repair progress reflects actual placements only."""
import pytest
from cifereca.plugin_api import LibraryMutationResult, MaintenancePlan
from cifereca_library_audits import NamingRepairTool


class Broker:
    def __init__(self):
        self.requests = []
        self.cancel_on_final = False
        self.control = None
        self.fail_at = None

    def plan(self, request):
        self.requests.append(request)
        return request

    def run(self, request, control):
        if len(self.requests) == self.fail_at:
            raise RuntimeError("/private/owner/library.epub")
        if self.cancel_on_final and len(self.requests) == 1:
            control.stopped = True
        return LibraryMutationResult(True, changed=1, summary="ok")


class Context:
    def __init__(self, broker):
        self.mutation = broker

    def broker(self, name):
        assert name == "library.maintenance"
        return self.mutation


class Cancelled(Exception):
    pass


class Control:
    def __init__(self):
        self.events = []
        self.stopped = False

    def check_cancelled(self):
        if self.stopped:
            raise Cancelled()

    def report(self, **kwargs):
        self.events.append(kwargs)


def _run(ids, broker=None):
    broker = broker or Broker()
    control = Control()
    tool = NamingRepairTool().activate(Context(broker))
    plan = MaintenancePlan(tool_id=tool.info.id, summary="Plan", facts={"placement_ids": ids})
    return tool, plan, control, broker


def test_empty_naming_repair_has_no_fake_one_of_one():
    tool, plan, control, broker = _run(())
    result = tool.run(plan, control)
    assert result.ok and result.changed == 0 and result.facts["planned"] == 0
    assert broker.requests == []
    assert len(control.events) == 2
    assert all("current" not in e and "total" not in e for e in control.events)


def test_naming_repair_reports_exact_placement_count():
    tool, plan, control, broker = _run(("p1", "p2"))
    result = tool.run(plan, control)
    assert result.ok and result.changed == 2
    assert [(e["current"], e["total"]) for e in control.events] == [(0, 2), (1, 2), (2, 2)]
    assert [r.placement_id for r in broker.requests] == ["p1", "p2"]


def test_naming_repair_failure_does_not_leak_private_exception():
    broker = Broker()
    broker.fail_at = 1
    tool, plan, control, _ = _run(("p1",), broker)
    result = tool.run(plan, control)
    assert not result.ok and result.facts["failures"] == 1
    assert "échec" in control.events[-1]["message"]
    assert all("/private/" not in x for x in result.warnings)
    assert all("/private/" not in e["message"] for e in control.events)


def test_naming_repair_cancellation_on_last_mutation_has_no_completion():
    broker = Broker()
    broker.cancel_on_final = True
    tool, plan, control, _ = _run(("p1",), broker)
    with pytest.raises(Cancelled):
        tool.run(plan, control)
    assert [(e["current"], e["total"]) for e in control.events] == [(0, 1)]
