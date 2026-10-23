"""P1 #2804: actual in-plan progress, not invented run() success."""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import CoverAuditTool, LanguageAuditTool, NamingAuditTool


class Data:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def catalog_entries(self, **kwargs):
        self.calls.append(("catalog", kwargs))
        return list(self.rows)

    def library_entries(self, **kwargs):
        self.calls.append(("library", kwargs))
        return list(self.rows)

    def preview(self, **kwargs):
        return "other.epub"


class Context:
    def __init__(self, count):
        self.data = Data([
            {"title": "PRIVATE_SECRET_TITLE", "filename": "PRIVATE_PATH.epub",
             "metadata": {"language": "fr"}}
            for _ in range(count)
        ])

    def broker(self, name):
        assert name in {"catalog", "metadata", "library.naming"}
        return self.data


class Control:
    def __init__(self, *, limit=None):
        self.events = []
        self.checks = 0
        self.limit = limit

    def check_cancelled(self):
        self.checks += 1
        if self.limit is not None and self.checks >= self.limit:
            raise RuntimeError("cancelled_synthetic")

    def report(self, **data):
        self.events.append(data)


@pytest.mark.parametrize("tool_type", [CoverAuditTool, LanguageAuditTool, NamingAuditTool])
@pytest.mark.parametrize("count", [0, 18, 600])
def test_progress_occurs_during_plan_and_is_bounded(tool_type, count):
    ctx = Context(count)
    tool = tool_type().activate(ctx)
    scope = MaintenanceContext(scope="library", identity_id="current")
    old_plan = tool.plan(scope)
    control = Control()
    plan = tool.plan_with_progress(scope, control)
    assert plan == old_plan
    assert control.checks >= 1 + count
    assert control.events
    assert "chargement" in control.events[0]["message"]
    numeric = [e for e in control.events if "total" in e]
    if count:
        assert numeric
        assert numeric[0]["current"] == 0
        assert numeric[0]["total"] == count
        assert numeric[-1]["current"] < count
        assert len(numeric) <= 2 + count // 16
        if count == 600:
            assert len(numeric) >= 25
            assert any(0 < e["current"] < count for e in numeric)
    else:
        assert numeric == []  # no invented 0/0 or fake 100%
    assert all(e.get("current", 0) <= e.get("total", count)
               for e in control.events)
    payload = json.dumps(control.events, ensure_ascii=False)
    assert "PRIVATE_SECRET_TITLE" not in payload
    assert "PRIVATE_PATH" not in payload
    assert "current" not in repr(old_plan) or count > 0


@pytest.mark.parametrize("tool_type", [CoverAuditTool, LanguageAuditTool, NamingAuditTool])
def test_cancellation_checked_inside_loop_without_publishing_success(tool_type):
    ctx = Context(600)
    tool = tool_type().activate(ctx)
    scope = MaintenanceContext(scope="library")
    control = Control(limit=22)
    with pytest.raises(RuntimeError, match="cancelled_synthetic"):
        tool.plan_with_progress(scope, control)
    assert control.events
    assert len(control.events) <= 4
    assert all(e.get("current", 0) < 600 for e in control.events)
    assert ctx.data.calls


def test_readonly_run_keeps_old_no_fake_one_of_one_contract():
    ctx = Context(18)
    tool = CoverAuditTool().activate(ctx)
    plan = tool.plan_with_progress(MaintenanceContext(), Control())
    control = Control()
    result = tool.run(plan, control)
    assert result.ok
    assert control.events == [{"message": plan.summary}]
