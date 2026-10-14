"""#1497: a malformed naming repair plan never reaches Core mutations.

Only synthetic brokers. No filesystem write, provider account or GTK usage.
"""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import LibraryMutationResult, MaintenancePlan
from cifereca_library_audits import NamingRepairTool


class StringBomb:
    def __str__(self):
        raise AssertionError("untrusted placement must not be stringified")


class MutationBroker:
    def __init__(self):
        self.requests = []

    def plan(self, request):
        self.requests.append(request)
        return request

    def run(self, request, control):
        return LibraryMutationResult(
            True, changed=1, summary="PRIVATE_CORE_RENAME_RESPONSE",
        )


class Context:
    def __init__(self):
        self.mutation = MutationBroker()
        self.broker_calls = 0

    def broker(self, name):
        self.broker_calls += 1
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


def _run(placement_ids, failures=0, warnings=("PRIVATE_PLAN_TOKEN",)):
    ctx = Context()
    tool = NamingRepairTool().activate(ctx)
    plan = MaintenancePlan(
        tool_id=tool.info.id, summary="PRIVATE_PLAN_TITLE",
        warnings=warnings,
        facts={"placement_ids": placement_ids, "policy_failures": failures},
    )
    control = Control()
    result = tool.run(plan, control)
    return ctx, control, result


@pytest.mark.parametrize("ids,failures", [
    (None, 0),
    ("PRIVATE_PLACEMENT", 0),
    ({"PRIVATE_PLACEMENT": True}, 0),
    ([StringBomb()], 0),
    ([False], 0),
    ([None], 0),
    ([12], 0),
    ([""], 0),
    (["  "], 0),
    ([" bad-placement"], 0),
    (["bad-placement "], 0),
    (["bad\nPRIVATE_TOKEN"], 0),
    (["x" * 1025], 0),
    (["same", "same"], 0),
    (["valid", StringBomb()], 0),
    (["valid"], True),
    (["valid"], -1),
    (["valid"], "PRIVATE_CREDENTIAL"),
    (["valid"], "1" * 30),
    (["valid"], [1]),
    (["valid"], StringBomb()),
])
def test_untrusted_plan_never_performs_renaming_or_invents_fraction(ids, failures):
    ctx, control, result = _run(ids, failures)
    assert ctx.broker_calls == 0
    assert ctx.mutation.requests == []
    assert not result.ok and result.changed == 0
    assert result.facts == {"planned": 0, "failures": 1, "invalid_plan": 1}
    assert result.warnings == (
        "Plan de renormalisation invalide : aucune mutation.",
    )
    assert result.summary == "Aucun placement renormalisé : plan invalide."
    assert control.events == [{"message": "Renormalisation impossible : plan invalide"}]
    assert all("current" not in event and "total" not in event
               for event in control.events)
    public = json.dumps({
        "summary": result.summary,
        "warnings": result.warnings,
        "facts": result.facts,
        "activity": control.events,
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "TOKEN", "CREDENTIAL"):
        assert secret not in public


def test_valid_plan_keeps_core_renames_and_real_progress():
    ctx, control, result = _run(
        ("PRIVATE_PLACEMENT_1", "PRIVATE_PLACEMENT_2"),
        warnings=(),
    )
    assert result.ok and result.changed == 2
    assert result.facts == {"planned": 2, "failures": 0}
    assert ctx.broker_calls == 1
    assert [(req.operation, req.placement_id) for req in ctx.mutation.requests] == [
        ("normalize_filename", "PRIVATE_PLACEMENT_1"),
        ("normalize_filename", "PRIVATE_PLACEMENT_2"),
    ]
    assert [(event["current"], event["total"]) for event in control.events] == [
        (0, 2), (1, 2), (2, 2),
    ]
    assert "PRIVATE" not in result.summary + str(result.warnings)


def test_legacy_numeric_string_failure_count_preserves_partial_failure():
    ctx, control, result = _run(["PRIVATE_PLACEMENT"], "2", warnings=())
    assert ctx.broker_calls == 1
    assert not result.ok and result.changed == 1
    assert result.facts == {"planned": 1, "failures": 0, "policy_failures": 2}
    assert "2 proposition(s) indisponible(s)" in control.events[-1]["message"]


def test_empty_plan_keeps_indeterminate_zero_item_progress():
    ctx, control, result = _run((), warnings=())
    assert ctx.mutation.requests == []
    assert result.ok and result.changed == 0
    assert result.facts == {"planned": 0, "failures": 0}
    assert len(control.events) == 2
    assert all("current" not in event and "total" not in event
               for event in control.events)


def test_cancellation_precedes_invalid_plan_error_result():
    ctx = Context()
    tool = NamingRepairTool().activate(ctx)
    plan = MaintenancePlan(
        tool_id=tool.info.id, summary="Plan",
        facts={"placement_ids": [StringBomb()]},
    )
    control = Control()
    control.stopped = True
    with pytest.raises(Cancelled):
        tool.run(plan, control)
    assert control.events == []
    assert ctx.mutation.requests == []
