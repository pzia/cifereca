"""#1497/#1498: untrusted Core changed-counts cannot fake mutation progress.

No real filesystem changes; all Core responses are synthetic.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import MaintenancePlan
from cifereca_library_audits import DuplicateCleanupTool


class PoisonCount:
    def __int__(self):
        raise RuntimeError("Bearer PRIVATE_PASSWORD /home/PRIVATE_USER")


class Broker:
    def __init__(self, changed):
        self.changed = changed
        self.requests = []

    def plan(self, request):
        self.requests.append(request)
        return request

    def run(self, request, control):
        return NS(ok=True, changed=self.changed, warnings=(
            "/home/PRIVATE_USER/secret.epub", "Bearer PRIVATE_ACCESS_TOKEN",
        ))


class Context:
    def __init__(self, broker):
        self.mutation = broker

    def broker(self, name):
        assert name == "library.maintenance"
        return self.mutation


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        pass

    def report(self, **event):
        self.events.append(event)


@pytest.mark.parametrize("bad_count", (
    -1, True, False, "2", None, 2.0, 1_000_001, PoisonCount(),
))
def test_invalid_changed_count_fails_without_fabricating_completed_mutations(bad_count):
    broker = Broker(bad_count)
    tool = DuplicateCleanupTool().activate(Context(broker))
    plan = MaintenancePlan(
        tool_id=tool.info.id, summary="Bearer PRIVATE_PLAN",
        facts={"pairs": (("PRIVATE_DUPLICATE", "PRIVATE_KEEPER"),)},
    )
    control = Control()
    result = tool.run(plan, control)
    assert len(broker.requests) == 1
    assert result.changed == 0
    assert result.ok is False
    assert result.facts["planned"] == 1
    assert result.facts["failures"] == 1
    assert [(e.get("current"), e.get("total")) for e in control.events] == [
        (0, 1), (1, 1),
    ]
    assert "échec" in control.events[-1]["message"]
    public = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "progress": control.events,
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "/home/", "Password", "RuntimeError"):
        assert secret not in public


@pytest.mark.parametrize("good_count", (0, 1))
def test_valid_core_mutation_count_preserves_real_progress(good_count):
    broker = Broker(good_count)
    tool = DuplicateCleanupTool().activate(Context(broker))
    plan = MaintenancePlan(
        tool_id=tool.info.id, summary="Private context excluded",
        facts={"pairs": (("PRIVATE_DUPLICATE", "PRIVATE_KEEPER"),)},
    )
    control = Control()
    result = tool.run(plan, control)
    assert result.ok is True
    assert result.changed == good_count
    assert result.facts == {"planned": 1, "failures": 0}
    assert [(e.get("current"), e.get("total")) for e in control.events] == [
        (0, 1), (1, 1),
    ]
