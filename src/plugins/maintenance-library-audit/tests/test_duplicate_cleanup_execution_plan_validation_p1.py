"""#1498: cached/untrusted destructive cleanup plans must fail closed.

The synthetic Core mutation broker tracks requests; no file deletion occurs.
"""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import LibraryMutationResult, MaintenancePlan
from cifereca_library_audits import DuplicateCleanupTool


class StringBomb:
    def __str__(self):
        raise AssertionError("untrusted placement must not be stringified")


class Maintenance:
    def __init__(self):
        self.requests = []

    def plan(self, request):
        self.requests.append(request)
        return request

    def run(self, request, control):
        return LibraryMutationResult(
            True, changed=1, summary="PRIVATE_CORE_MUTATION_SUMMARY"
        )


class Context:
    def __init__(self):
        self.core = Maintenance()
        self.broker_calls = 0

    def broker(self, name):
        self.broker_calls += 1
        assert name == "library.maintenance"
        return self.core


class Cancelled(Exception):
    pass


class Control:
    def __init__(self):
        self.events = []
        self.stopped = False

    def check_cancelled(self):
        if self.stopped:
            raise Cancelled()

    def report(self, **event):
        self.events.append(event)


def _run(pairs, warnings=("PRIVATE_PLAN_TOKEN",)):
    ctx = Context()
    tool = DuplicateCleanupTool().activate(ctx)
    plan = MaintenancePlan(
        tool_id=tool.info.id, summary="PRIVATE_PLAN_TITLE",
        warnings=warnings, facts={"pairs": pairs},
    )
    control = Control()
    outcome = tool.run(plan, control)
    return ctx, control, outcome


@pytest.mark.parametrize("pairs", [
    None,
    "PRIVATE_PAIR",
    {"PRIVATE_PLACEMENT": "PRIVATE_KEEPER"},
    [StringBomb()],
    [["one"]],
    [["one", "two", "three"]],
    [[StringBomb(), "keep"]],
    [[True, "keep"]],
    [[1, "keep"]],
    [[None, "keep"]],
    [["", "keep"]],
    [["  ", "keep"]],
    [[" duplicate", "keep"]],
    [["duplicate", "keep\nPRIVATE_TOKEN"]],
    [["duplicate", "duplicate"]],
    [["duplicate", "KEEPER" * 200]],
    [("d1", "k1"), ("d1", "k2")],
    [("d1", "d2"), ("d2", "k3")],
    [("d1", "k1"), ("d2", "k2"), (False, "PRIVATE_KEEPER")],
])
def test_corrupt_plan_never_calls_mutation_broker_or_reports_fake_progress(pairs):
    ctx, control, outcome = _run(pairs)
    assert ctx.broker_calls == 0
    assert ctx.core.requests == []
    assert not outcome.ok and outcome.changed == 0
    assert outcome.facts == {
        "planned": 0, "failures": 1, "invalid_plan": 1,
    }
    assert outcome.summary == "Aucun placement supprimé : plan de doublons invalide."
    assert outcome.warnings == ("Plan de doublons invalide : aucune suppression.",)
    assert control.events == [
        {"message": "Nettoyage impossible : plan de doublons invalide"}
    ]
    assert all("current" not in event and "total" not in event
               for event in control.events)
    public = json.dumps({
        "summary": outcome.summary, "warnings": outcome.warnings,
        "facts": outcome.facts, "activity": control.events,
    }, ensure_ascii=False)
    for private in ("PRIVATE", "Bearer", "TOKEN", "d1", "k1", "KEEPER"):
        assert private not in public


def test_valid_plan_preserves_progress_and_core_revalidation():
    ctx, control, outcome = _run(
        (("PRIVATE_DELETE_1", "PRIVATE_KEEP"), ("PRIVATE_DELETE_2", "PRIVATE_KEEP")),
        warnings=(),
    )
    assert outcome.ok and outcome.changed == 2
    assert outcome.facts == {"planned": 2, "failures": 0}
    assert [(e["current"], e["total"]) for e in control.events] == [
        (0, 2), (1, 2), (2, 2),
    ]
    assert ctx.broker_calls == 1
    assert [
        (req.operation, req.placement_id, req.keep_placement_id)
        for req in ctx.core.requests
    ] == [
        ("delete_duplicate", "PRIVATE_DELETE_1", "PRIVATE_KEEP"),
        ("delete_duplicate", "PRIVATE_DELETE_2", "PRIVATE_KEEP"),
    ]
    assert "PRIVATE" not in str(outcome.warnings) + outcome.summary


def test_empty_plan_is_successful_without_one_of_one_progress():
    ctx, control, outcome = _run((), warnings=())
    assert outcome.ok and outcome.changed == 0
    assert outcome.facts == {"planned": 0, "failures": 0}
    assert ctx.core.requests == []
    assert len(control.events) == 2
    assert all("total" not in event and "current" not in event
               for event in control.events)


def test_cancellation_before_invalid_plan_result_does_not_signal_success():
    ctx = Context()
    tool = DuplicateCleanupTool().activate(ctx)
    plan = MaintenancePlan(
        tool_id=tool.info.id, summary="Plan",
        facts={"pairs": [("duplicate", "duplicate")]},
    )
    control = Control()
    control.stopped = True
    with pytest.raises(Cancelled):
        tool.run(plan, control)
    assert control.events == []
    assert ctx.core.requests == []
