"""#1497 — rename outcomes require explicit bool status and attested count.

All mutation broker responses are synthetic. No filesystem changes.
"""
from types import SimpleNamespace as NS
import pytest

from cifereca.plugin_api import MaintenancePlan
from cifereca_library_audits import NamingRepairTool


class Broker:
    def __init__(self, results):
        self.results = iter(results)
        self.requests = []

    def plan(self, request):
        self.requests.append(request)
        return request

    def run(self, request, control):
        return next(self.results)


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        pass

    def report(self, **event):
        self.events.append(event)


def run(results):
    broker = Broker(results)
    tool = NamingRepairTool().activate(NS(broker=lambda name: broker))
    ids = tuple(f"PRIVATE_PLACEMENT_{i}" for i in range(len(results)))
    plan = MaintenancePlan(
        tool_id=tool.info.id, summary="Bearer PRIVATE_TOKEN",
        facts={"placement_ids": ids},
    )
    control = Control()
    return tool.run(plan, control), broker, control


@pytest.mark.parametrize("bad", (
    NS(changed=1),
    NS(ok=None, changed=1),
    NS(ok="yes", changed=1),
    NS(ok=1, changed=1),
    NS(ok=[], changed=1),
    NS(ok=True),
))
def test_rename_outcome_without_explicit_status_fails_closed(bad):
    result, broker, control = run((bad,))
    assert len(broker.requests) == 1
    assert result.ok is False and result.changed == 0
    assert result.facts == {"planned": 1, "failures": 1}
    assert [(e.get("current"), e.get("total")) for e in control.events] == [
        (0, 1), (1, 1),
    ]
    assert "échec" in control.events[-1]["message"]
    assert "PRIVATE" not in str(result.summary) + str(result.warnings) + str(control.events)


def test_one_unknown_outcome_does_not_hide_other_confirmed_rename():
    result, broker, control = run((
        NS(changed=1), NS(ok=True, changed=1, warnings=()),
    ))
    assert len(broker.requests) == 2
    assert not result.ok and result.changed == 1
    assert result.facts == {"planned": 2, "failures": 1}
    assert "échec" in control.events[-1]["message"]


def test_failed_but_partially_applied_result_still_counts_attested_change():
    result, broker, control = run((NS(ok=False, changed=1, warnings=()),))
    assert result.ok is False and result.changed == 1
    assert result.facts == {"planned": 1, "failures": 1}
    assert "échec" in control.events[-1]["message"]


def test_explicit_success_keeps_existing_status_and_counts():
    result, broker, control = run((NS(ok=True, changed=1, warnings=()),))
    assert result.ok is True and result.changed == 1
    assert result.facts == {"planned": 1, "failures": 0}
    assert control.events[-1]["message"] == "Renormalisation terminée"
