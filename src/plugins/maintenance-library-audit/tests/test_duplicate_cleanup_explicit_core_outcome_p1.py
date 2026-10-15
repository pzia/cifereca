"""#1498: destructive Core mutation results require an explicit bool outcome.

Fake Core broker and operation control only; never removes local files.
"""
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import MaintenancePlan
from cifereca_library_audits import DuplicateCleanupTool


class Broker:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.requests = []

    def plan(self, request):
        self.requests.append(request)
        return request

    def run(self, request, control):
        return next(self.outcomes)


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        pass

    def report(self, **event):
        self.events.append(event)


def execute(outcomes):
    broker = Broker(outcomes)
    tool = DuplicateCleanupTool().activate(NS(broker=lambda name: broker))
    pairs = tuple((f"PRIVATE_DUPLICATE_{i}", "PRIVATE_KEEPER") for i, _ in enumerate(outcomes))
    plan = MaintenancePlan(
        tool_id=tool.info.id,
        summary="Bearer PRIVATE_TOKEN /home/private",
        warnings=("PRIVATE_PASSWORD",),
        facts={"pairs": pairs},
    )
    control = Control()
    return tool.run(plan, control), broker, control


@pytest.mark.parametrize("bad", (
    NS(changed=1),  # missing explicit Core success status
    NS(ok=None, changed=1),
    NS(ok="true", changed=1),
    NS(ok=1, changed=1),
    NS(ok=[], changed=1),
    NS(ok=True),  # no attested mutation count
))
def test_unattested_deletion_outcome_never_claims_success(bad):
    result, broker, control = execute((bad,))
    assert len(broker.requests) == 1
    assert result.changed == 0 and result.ok is False
    assert result.facts == {"planned": 1, "failures": 1}
    assert [(e.get("current"), e.get("total")) for e in control.events] == [(0, 1), (1, 1)]
    assert "échec" in control.events[-1]["message"]
    public = json.dumps({
        "summary": result.summary,
        "warnings": result.warnings,
        "facts": result.facts,
        "events": control.events,
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "/home/", "TOKEN", "PASSWORD"):
        assert secret not in public


def test_unverified_one_pair_does_not_erase_later_attested_deletion():
    result, broker, control = execute((
        NS(changed=1),  # missing ok
        NS(ok=True, changed=1, warnings=()),
    ))
    assert len(broker.requests) == 2
    assert result.ok is False and result.changed == 1
    assert result.facts == {"planned": 2, "failures": 1}
    assert "échec" in control.events[-1]["message"]


def test_explicit_failure_can_still_attest_a_partial_change():
    result, _, control = execute((NS(ok=False, changed=1, warnings=()),))
    assert result.ok is False and result.changed == 1
    assert result.facts == {"planned": 1, "failures": 1}
    assert "échec" in control.events[-1]["message"]


def test_explicit_success_retains_nominal_count():
    result, _, control = execute((NS(ok=True, changed=1, warnings=()),))
    assert result.ok is True and result.changed == 1
    assert result.facts == {"planned": 1, "failures": 0}
    assert control.events[-1]["message"] == "Nettoyage terminé"
