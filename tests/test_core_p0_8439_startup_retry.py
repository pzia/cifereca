"""Headless startup retry and receipt preservation."""
import sys
from types import SimpleNamespace as NS

from cifereca.gui import source_inventory_startup as startup
from cifereca.plugin_api import SyncResult
from tests.test_core_p0_1346_kobo_on_start_inventory_gui import _startup_window


def test_startup_busy_retries_once_and_preserves_partial_count(monkeypatch):
    r, _, _ = _startup_window()
    timers = []
    monkeypatch.setitem(sys.modules, "gi.repository", NS(
        GLib=NS(timeout_add_seconds=lambda delay, cb: timers.append((delay, cb)))))
    busy = SyncResult(ok=False, facts={"status": "busy", "deferred": 1})
    partial = SyncResult(ok=False, changed=5, facts={"status": "partial", "deferred": 1})
    receipts = iter([busy, partial])
    r.broker.synchronize_scheduled_inventory = lambda *a: next(receipts)
    assert startup.dispatch_after_opening(r.window)
    first = r.jobs.pop()
    result = first.work()
    assert result.facts["status"] == "busy"
    first.done(result)
    assert len(timers) == 1
    delay, retry = timers.pop()
    assert delay == 60 and retry() is False
    second = r.jobs.pop()
    result = second.work()
    assert result.facts["status"] == "partial" and result.changed == 5
    second.done(result)
    assert not timers and not r.jobs
