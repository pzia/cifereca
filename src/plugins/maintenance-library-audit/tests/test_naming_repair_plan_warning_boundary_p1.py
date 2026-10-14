"""#1497: naming repair results must never echo untrusted cached plan warnings.

Uses a synthetic broker; no real filesystem mutations, credentials or GTK.
"""
import json

import pytest

from cifereca.plugin_api import LibraryMutationResult, MaintenancePlan
from cifereca_library_audits import NamingRepairTool


class Broker:
    def __init__(self):
        self.requests = []

    def plan(self, request):
        self.requests.append(request)
        return request

    def run(self, request, control):
        return LibraryMutationResult(ok=True, changed=1, summary="PRIVATE_CORE_FILENAME")


class Context:
    def __init__(self):
        self.mutation = Broker()
        self.calls = 0

    def broker(self, name):
        self.calls += 1
        assert name == "library.maintenance"
        return self.mutation


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        pass

    def report(self, **kwargs):
        self.events.append(kwargs)


def run_plan(facts, warnings):
    ctx = Context()
    control = Control()
    tool = NamingRepairTool().activate(ctx)
    plan = MaintenancePlan(
        tool_id=tool.info.id,
        summary="PRIVATE_PLAN_FILENAME",
        warnings=warnings,
        facts=facts,
    )
    result = tool.run(plan, control)
    return ctx, control, result


def test_private_cached_warning_is_replaced_by_safe_plan_counters():
    ctx, control, result = run_plan(
        {
            "placement_ids": ("PRIVATE_PLACEMENT_ID",),
            "skipped": 2,
            "policy_failures": 3,
            "read_failures": 1,
        },
        (
            "Bearer PRIVATE_ACCESS_TOKEN",
            "/home/PRIVATE_USER/book.epub",
            "UNKNOWN_UNSAFE_WARNING",
        ),
    )
    assert ctx.calls == 1
    assert len(ctx.mutation.requests) == 1
    assert result.changed == 1 and not result.ok
    assert result.warnings == (
        "2 entrée(s) non vérifiable(s) ignorée(s).",
        "3 placement(s) sans proposition valide de la politique Core.",
        "1 artefact(s) non inspecté(s) : broker library.read en erreur.",
    )
    assert [(e["current"], e["total"]) for e in control.events] == [(0, 1), (1, 1)]
    exported = json.dumps({
        "summary": result.summary,
        "warnings": result.warnings,
        "facts": result.facts,
        "events": control.events,
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "/home/", "UNKNOWN_UNSAFE_WARNING"):
        assert secret not in exported


def test_legacy_plan_without_counts_remains_usable_but_drops_unsafe_warnings():
    ctx, control, result = run_plan(
        {"placement_ids": ("p1",)},
        ("PRIVATE_PAST_WARNING",),
    )
    assert result.ok and result.changed == 1
    assert result.warnings == ()
    assert len(ctx.mutation.requests) == 1


@pytest.mark.parametrize("name,value", [
    ("skipped", True),
    ("skipped", -1),
    ("skipped", "PRIVATE_PATH"),
    ("skipped", 1_000_001),
    ("read_failures", None),
    ("read_failures", False),
    ("read_failures", ["PRIVATE_PATH"]),
    ("read_failures", 1_000_001),
])
def test_invalid_diagnostic_counters_reject_plan_before_mutation(name, value):
    ctx, control, result = run_plan(
        {"placement_ids": ("p1",), name: value},
        ("Bearer PRIVATE_TOKEN",),
    )
    assert not result.ok and result.changed == 0
    assert result.facts == {"planned": 0, "failures": 1, "invalid_plan": 1}
    assert ctx.calls == 0 and ctx.mutation.requests == []
    assert control.events == [{"message": "Renormalisation impossible : plan invalide"}]
    assert "PRIVATE" not in str(result.warnings)
