"""#1498: do not export arbitrary warning strings from a cached cleanup plan.

Synthetic broker only; no real deletion or user data.
"""
import json

import pytest

from cifereca.plugin_api import LibraryMutationResult, MaintenancePlan
from cifereca_library_audits import DuplicateCleanupTool


NOTICE = "Chaque suppression est revérifiée par Core comme doublon du même artefact."


class Broker:
    def __init__(self):
        self.requests = []

    def plan(self, request):
        self.requests.append(request)
        return request

    def run(self, request, control):
        return LibraryMutationResult(ok=True, changed=1, summary="PRIVATE_CORE_BOOK")


class Context:
    def __init__(self):
        self.broker_calls = 0
        self.mutation = Broker()

    def broker(self, name):
        self.broker_calls += 1
        assert name == "library.maintenance"
        return self.mutation


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        pass

    def report(self, **kwargs):
        self.events.append(kwargs)


def run_plan(warnings, read_failures=None):
    ctx = Context()
    ctl = Control()
    tool = DuplicateCleanupTool().activate(ctx)
    facts = {"pairs": (("PRIVATE_DUPLICATE", "PRIVATE_KEEP"),)}
    if read_failures is not None:
        facts["read_failures"] = read_failures
    result = tool.run(
        MaintenancePlan(
            tool_id=tool.info.id,
            summary="PRIVATE_PLAN_TITLE",
            facts=facts,
            warnings=warnings,
        ), ctl,
    )
    return ctx, ctl, result


def test_cached_private_warnings_are_not_exposed_and_known_diagnostics_remain():
    ctx, ctl, result = run_plan(
        (NOTICE, "Bearer PRIVATE_ACCOUNT_TOKEN", "/home/PRIVATE_USER/file.epub"),
        read_failures=3,
    )
    assert result.ok and result.changed == 1
    assert len(ctx.mutation.requests) == 1
    assert result.warnings == (
        NOTICE,
        "3 artefact(s) non inspecté(s) : broker library.read en erreur.",
    )
    public = json.dumps({
        "summary": result.summary,
        "warnings": result.warnings,
        "facts": result.facts,
        "events": ctl.events,
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "/home/"):
        assert secret not in public


def test_legacy_plan_without_notice_keeps_original_result_shape():
    _, ctl, result = run_plan(("PRIVATE_PAST_WARNING",))
    assert result.ok and result.changed == 1
    assert result.warnings == ()
    assert [(e["current"], e["total"]) for e in ctl.events] == [(0, 1), (1, 1)]


@pytest.mark.parametrize("bad_count", [
    True, False, -2, "Bearer PRIVATE_TOKEN", 1_000_001,
])
def test_malformed_read_failure_count_cannot_be_exported(bad_count):
    _, ctl, result = run_plan(
        ("Bearer PRIVATE_TOKEN",), read_failures=bad_count,
    )
    assert result.ok and result.changed == 1
    assert result.warnings == ()
    assert "PRIVATE" not in result.summary
    assert all("PRIVATE" not in e["message"] for e in ctl.events)
