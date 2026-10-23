"""P1 #2804: original destructive and integrity plans acquire no new rights.

Progress follows only the metadata inventory already used by each tool;
no extra library.read, disk hash, rename, delete or provider fetch.
"""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import (
    IntegrityAuditTool, NamingRepairTool, DuplicateCleanupTool,
)


class Data:
    def __init__(self, count):
        self.rows = [
            {"artifact_id": "", "title": "PRIVATE_BOOK_TITLE",
             "filename": "PRIVATE_PERSON.epub", "local": False}
            for _ in range(count)
        ]
        self.reads = 0
        self.mutations = 0

    def library_entries(self, **kwargs):
        self.reads += 1
        return self.rows

    def for_artifact(self, identifier):
        raise AssertionError("no referenced artifact in fixture")

    def preview(self, **kwargs):
        raise AssertionError("no referenced placement in fixture")

    def __getattr__(self, name):
        if name in {"delete", "rename", "move", "apply"}:
            self.mutations += 1
            raise AssertionError("no mutation during plan")
        raise AttributeError(name)


class Context:
    def __init__(self, count):
        self.data = Data(count)

    def broker(self, name):
        assert name in {"metadata", "library.read", "library.naming"}
        return self.data


class Control:
    def __init__(self, cancel_after=None):
        self.events = []
        self.checks = 0
        self.cancel_after = cancel_after

    def report(self, **fields):
        self.events.append(fields)

    def check_cancelled(self):
        self.checks += 1
        if self.cancel_after is not None and self.checks >= self.cancel_after:
            raise RuntimeError("cancelled_fixture")


@pytest.mark.parametrize("tool_type", [
    IntegrityAuditTool, NamingRepairTool, DuplicateCleanupTool,
])
@pytest.mark.parametrize("count", [0, 18, 600])
def test_exact_same_plan_and_live_scan_counts(tool_type, count):
    ctx = Context(count)
    tool = tool_type().activate(ctx)
    scope = MaintenanceContext(scope="library", identity_id="current")
    original = tool.plan(scope)
    control = Control()
    progressive = tool.plan_with_progress(scope, control)
    assert progressive == original
    assert ctx.data.reads == 2
    assert ctx.data.mutations == 0
    assert control.checks >= count + 1
    assert control.events and "chargement" in control.events[0]["message"]
    numeric = [e for e in control.events if "total" in e]
    if count:
        assert numeric[0]["current"] == 0
        assert numeric[0]["total"] == count
        assert numeric[-1]["current"] < count
        assert len(numeric) <= count // 16 + 2
        if count == 600:
            assert len(numeric) >= 25
    else:
        assert numeric == []
    assert all(e.get("current", 0) < e.get("total", 1)
               for e in numeric)
    text = json.dumps(control.events, ensure_ascii=False)
    assert "PRIVATE_BOOK" not in text
    assert "PRIVATE_PERSON" not in text
    assert "artifact_id" not in text


@pytest.mark.parametrize("tool_type", [
    IntegrityAuditTool, NamingRepairTool, DuplicateCleanupTool,
])
def test_preinventory_can_cancel_while_plan_is_scanning(tool_type):
    ctx = Context(600)
    tool = tool_type().activate(ctx)
    control = Control(cancel_after=21)
    with pytest.raises(RuntimeError, match="cancelled_fixture"):
        tool.plan_with_progress(MaintenanceContext(scope="library"), control)
    assert ctx.data.reads == 1
    assert ctx.data.mutations == 0
    assert control.events
    assert len(control.events) <= 3


def test_no_new_authority_for_destructive_duplicate_revalidation():
    ctx = Context(18)
    tool = DuplicateCleanupTool().activate(ctx)
    plan = tool.plan_with_progress(MaintenanceContext(scope="library"), Control())
    assert plan.changes == ()
    assert ctx.data.mutations == 0
