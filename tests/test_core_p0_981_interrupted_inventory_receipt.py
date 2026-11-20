"""#981: an interrupted scheduled inventory cannot mint a global success."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui import source_inventory_startup as startup
from cifereca.gui.task_runner import TaskRunner
from cifereca.plugin_api import SyncResult
from tests.test_core_p0_1346_kobo_on_start_inventory_gui import _rig, _phase


def _queue(trigger, count):
    accounts = [
        NS(identity_id=identity, source_id="sync:" + character * 24,
           provider_id="kobo", connected=True, enabled=True, sync_capable=True,
           status="idle", last_attempt="", phase_states=(_phase(),))
        for identity, character in (("alice", "a"), ("bob", "b"))[:count]
    ]
    r = _rig(mode="on_start" if trigger == "startup" else "periodic", accounts=accounts)
    if trigger == "startup":
        assert startup.dispatch_after_opening(r.window)
    else:
        r.configure()
        assert next(iter(r.records["timers"].values()))()
    assert len(r.jobs) == 1
    return r


@pytest.mark.parametrize("trigger", ["startup", "periodic"])
@pytest.mark.parametrize("count", [1, 2])
@pytest.mark.parametrize("change", ["closed", "broker"])
def test_interruption_after_first_or_only_receipt_is_partial(trigger, count, change):
    r = _queue(trigger, count)
    calls = []

    def dispatch(identity, actual_trigger):
        calls.append((identity, actual_trigger))
        if change == "closed":
            r.window._closed = True
        else:
            r.window.plugin_manager.broker = lambda name: None
        return SyncResult(ok=True, facts={"attempted": 1, "private": "DO_NOT_ECHO"})

    r.broker.synchronize_scheduled_inventory = dispatch
    result = r.jobs[0].work()
    assert calls == [("alice", trigger)]
    assert result.ok is False and result.facts == {"status": "partial", "completed": 1}
    assert TaskRunner._source_sync_outcome(result, "source-sync") == "partial"
    assert "DO_NOT_ECHO" not in repr(result)


@pytest.mark.parametrize("trigger", ["startup", "periodic"])
@pytest.mark.parametrize("attempted", [0, 1])
def test_no_interruption_keeps_attested_success_or_noop(trigger, attempted):
    r = _queue(trigger, 2)
    calls = []

    def dispatch(identity, actual_trigger):
        calls.append((identity, actual_trigger))
        return SyncResult(ok=True, facts={"attempted": attempted})

    r.broker.synchronize_scheduled_inventory = dispatch
    result = r.jobs[0].work()
    assert calls == [("alice", trigger), ("bob", trigger)]
    assert TaskRunner._source_sync_outcome(result, "source-sync") == (
        "success" if attempted else "cancelled")


def test_startup_rechecks_broker_after_policy_probe_before_dispatch():
    r = _queue("startup", 2)
    original = r.broker.inventory_refresh_schedule

    def revoked(sid):
        value = original(sid)
        r.window.plugin_manager.broker = lambda name: None
        return value

    r.broker.inventory_refresh_schedule = revoked
    assert r.jobs[0].work() is None
    assert not any(call[0] == "dispatch" for call in r.records["calls"])
