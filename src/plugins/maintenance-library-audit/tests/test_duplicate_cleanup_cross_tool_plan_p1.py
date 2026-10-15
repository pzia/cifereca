"""#1498: destructive cleanup never executes a plan for another tool.

No real filesystem or provider account: the Core broker throws if accessed.
"""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import MaintenancePlan
from cifereca_library_audits import DuplicateCleanupTool


class PoisonTool:
    def __eq__(self, other):
        raise AssertionError("Never call equality on attacker-supplied tool id")


class Context:
    def __init__(self):
        self.broker_calls = []

    def broker(self, name):
        self.broker_calls.append(name)
        raise AssertionError("Invalid duplicate plan reached Core library.maintenance")


class Control:
    def __init__(self):
        self.events = []
        self.cancelled = False

    def check_cancelled(self):
        if self.cancelled:
            raise KeyboardInterrupt("cancelled")

    def report(self, **event):
        self.events.append(event)


@pytest.mark.parametrize("wrong_id", (
    "library.naming.repair",
    "library.naming.audit",
    "languages.audit",
    "",
    "PRIVATE_ACCOUNT_TOOL",
    None,
    False,
    PoisonTool(),
))
def test_another_tools_plan_with_valid_pairs_never_deletes(wrong_id):
    context, control = Context(), Control()
    tool = DuplicateCleanupTool().activate(context)
    plan = MaintenancePlan(
        tool_id=wrong_id,
        summary="Bearer PRIVATE_PLAN_TITLE",
        warnings=("/home/PRIVATE_USER/books/secret.epub",),
        facts={
            "pairs": (("PRIVATE_DUPLICATE", "PRIVATE_KEEPER"),),
            "read_failures": 0,
        },
    )
    result = tool.run(plan, control)
    assert context.broker_calls == []
    assert not result.ok and result.changed == 0
    assert result.facts == {"planned": 0, "failures": 1, "invalid_plan": 1}
    assert control.events == [
        {"message": "Nettoyage impossible : plan de doublons invalide"}
    ]
    exported = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "progress": control.events,
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "/home/"):
        assert secret not in exported


@pytest.mark.parametrize("facts", (
    None,
    False,
    "PRIVATE_KEEPER",
    ("PRIVATE_DUPLICATE", "PRIVATE_KEEPER"),
    [{"pairs": (("PRIVATE_DUPLICATE", "PRIVATE_KEEPER"),)}],
))
def test_non_dictionary_facts_fail_closed(facts):
    context, control = Context(), Control()
    tool = DuplicateCleanupTool().activate(context)
    plan = MaintenancePlan(
        tool_id=tool.info.id,
        summary="PRIVATE_TITLE",
        facts=facts,
    )
    result = tool.run(plan, control)
    assert not result.ok and result.changed == 0
    assert result.facts["invalid_plan"] == 1
    assert context.broker_calls == []
    assert control.events == [
        {"message": "Nettoyage impossible : plan de doublons invalide"}
    ]


def test_cancellation_precedes_foreign_plan_rejection():
    context, control = Context(), Control()
    control.cancelled = True
    tool = DuplicateCleanupTool().activate(context)
    plan = MaintenancePlan(
        tool_id="library.naming.repair",
        summary="Bearer PRIVATE_TITLE",
        facts={"pairs": (("PRIVATE_DUPLICATE", "PRIVATE_KEEPER"),)},
    )
    with pytest.raises(KeyboardInterrupt):
        tool.run(plan, control)
    assert context.broker_calls == []
    assert control.events == []
