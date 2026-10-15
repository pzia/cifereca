"""#1497: unverified naming entries cannot become a complete repair.

Synthetic Core mutation broker; never touches files or actual library rows.
"""
from types import SimpleNamespace as NS

from cifereca.plugin_api import LibraryMutationResult, MaintenancePlan
from cifereca_library_audits import NamingRepairTool


class Broker:
    def __init__(self):
        self.calls = []

    def plan(self, request):
        self.calls.append(request)
        return request

    def run(self, request, control):
        return LibraryMutationResult(True, changed=1, summary="PRIVATE_LOCAL_PATH")


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        pass

    def report(self, **event):
        self.events.append(event)


def run(ids, skipped):
    broker = Broker()
    tool = NamingRepairTool().activate(NS(broker=lambda name: broker))
    plan = MaintenancePlan(
        tool_id=tool.info.id,
        summary="/home/PRIVATE/secret.epub",
        facts={"placement_ids": ids, "skipped": skipped},
        warnings=("Bearer PRIVATE_TOKEN",),
    )
    control = Control()
    return tool.run(plan, control), control, broker


def test_skipped_only_does_not_claim_naming_repair_complete():
    result, control, broker = run((), 2)
    assert broker.calls == []
    assert result.ok is False and result.changed == 0
    assert result.facts == {"planned": 0, "failures": 0, "skipped": 2}
    assert "2 entrée(s) ignorée(s)" in result.summary
    assert "incomplète" in control.events[-1]["message"]
    assert all("current" not in event and "total" not in event for event in control.events)
    assert "PRIVATE" not in str((result.summary, result.warnings, control.events))


def test_successful_one_rename_does_not_hide_unverified_other_row():
    result, control, broker = run(("placement-a",), 1)
    assert len(broker.calls) == 1
    assert result.ok is False and result.changed == 1
    assert result.facts == {"planned": 1, "failures": 0, "skipped": 1}
    assert control.events[0]["current"] == 0 and control.events[0]["total"] == 1
    assert control.events[-1]["current"] == control.events[-1]["total"] == 1
    assert "partielle" in control.events[-1]["message"]
    assert "1 entrée(s) ignorée(s)" in control.events[-1]["message"]


def test_no_skipped_and_successful_mutation_preserve_nominal_status():
    result, control, broker = run(("placement-a",), 0)
    assert result.ok is True
    assert result.changed == 1
    assert result.facts == {"planned": 1, "failures": 0}
    assert control.events[-1]["message"] == "Renormalisation terminée"
