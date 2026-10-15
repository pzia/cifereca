"""#1497: a restored preview for another tool cannot authorize file renaming.

Tests use a Core broker that fails on access. No disk, GTK or provider I/O.
"""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import MaintenancePlan
from cifereca_library_audits import NamingRepairTool


class MaliciousToolId:
    def __eq__(self, other):
        raise AssertionError("Never compare an untrusted object as tool id")


class Context:
    def __init__(self):
        self.broker_calls = []

    def broker(self, name):
        self.broker_calls.append(name)
        raise AssertionError("Invalid plan must never reach Core mutation broker")


class Control:
    def __init__(self):
        self.events = []
        self.cancelled = False

    def check_cancelled(self):
        if self.cancelled:
            raise KeyboardInterrupt("cancelled")

    def report(self, **kwargs):
        self.events.append(kwargs)


@pytest.mark.parametrize("wrong_id", (
    "library.duplicates.cleanup",
    "library.naming.audit",
    "languages.audit",
    "",
    "PRIVATE_ACCOUNT_TOOL",
    None,
    False,
    MaliciousToolId(),
))
def test_foreign_plan_with_valid_looking_placement_ids_never_mutates(wrong_id):
    ctx = Context()
    tool = NamingRepairTool().activate(ctx)
    plan = MaintenancePlan(
        tool_id=wrong_id,
        summary="Bearer PRIVATE_PLAN_TITLE",
        warnings=("/home/PRIVATE_USER/secret.epub",),
        facts={
            "placement_ids": ("PLACEMENT_ALICE",),
            "skipped": 0, "read_failures": 0, "policy_failures": 0,
        },
    )
    control = Control()
    result = tool.run(plan, control)
    assert ctx.broker_calls == []
    assert not result.ok and result.changed == 0
    assert result.facts == {"planned": 0, "failures": 1, "invalid_plan": 1}
    assert control.events == [{"message": "Renormalisation impossible : plan invalide"}]
    exported = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "progress": control.events,
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "PLACEMENT_ALICE", "/home/", "Bearer"):
        assert secret not in exported


@pytest.mark.parametrize("facts", (
    None,
    False,
    "PRIVATE_PLACEMENT",
    ("PRIVATE_PLACEMENT",),
    [{"placement_ids": ("PRIVATE_PLACEMENT",)}],
))
def test_non_dictionary_restored_facts_fail_closed(facts):
    ctx = Context()
    tool = NamingRepairTool().activate(ctx)
    plan = MaintenancePlan(
        tool_id=tool.info.id,
        summary="PRIVATE_PLAN",
        facts=facts,
    )
    control = Control()
    result = tool.run(plan, control)
    assert not result.ok and result.facts["invalid_plan"] == 1
    assert result.changed == 0
    assert ctx.broker_calls == []
    assert control.events == [{"message": "Renormalisation impossible : plan invalide"}]


def test_cancellation_on_foreign_plan_wins_without_reporting_failure():
    ctx = Context()
    tool = NamingRepairTool().activate(ctx)
    control = Control()
    control.cancelled = True
    plan = MaintenancePlan(
        tool_id="library.duplicates.cleanup", summary="PRIVATE_TITLE",
        facts={"placement_ids": ("p1",)},
    )
    with pytest.raises(KeyboardInterrupt):
        tool.run(plan, control)
    assert control.events == []
    assert ctx.broker_calls == []
