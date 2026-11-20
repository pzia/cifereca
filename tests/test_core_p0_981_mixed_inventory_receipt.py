"""#981: distinguish failed, partial, no-op and successful scheduled batches."""
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import SyncResult
from cifereca.gui import source_inventory_startup as startup
from tests.test_core_p0_1346_kobo_on_start_inventory_gui import _rig, _phase
from tests.test_core_p0_981_interrupted_inventory_receipt import _outcome


@pytest.mark.parametrize("trigger", ["startup", "periodic"])
@pytest.mark.parametrize("scenario", ["mixed_identity", "mixed_source", "all_failed", "noop", "success"])
def test_three_identity_batch_preserves_attested_progress(trigger, scenario):
    accounts = [
        NS(identity_id=identity, source_id="sync:" + character * 24,
           provider_id="kobo", enabled=True, connected=True, sync_capable=True,
           status="idle", last_attempt="", phase_states=(_phase(),))
        for identity, character in (("alice", "a"), ("bob", "b"), ("carol", "c"))
    ]
    r = _rig(mode="on_start" if trigger == "startup" else "periodic", accounts=accounts)
    calls = []

    def dispatch(identity, actual_trigger):
        calls.append((identity, actual_trigger))
        attempted, failed = {
            "mixed_identity": (1, int(identity == "bob")),
            "mixed_source": (2, 1) if identity == "alice" else (0, 0),
            "all_failed": (1, 1),
            "noop": (0, 0),
            "success": (1, 0),
        }[scenario]
        return SyncResult(ok=failed == 0, facts={
            "attempted": attempted, "failed": failed, "private": "DO_NOT_ECHO",
        })

    r.broker.synchronize_scheduled_inventory = dispatch
    if trigger == "startup":
        assert startup.dispatch_after_opening(r.window)
    else:
        r.configure()
        assert next(iter(r.records["timers"].values()))()
    assert len(r.jobs) == 1
    if scenario == "all_failed":
        with pytest.raises(RuntimeError) as error:
            r.jobs[0].work()
        assert "DO_NOT_ECHO" not in str(error.value)
    else:
        result = r.jobs[0].work()
        if scenario in ("mixed_identity", "mixed_source"):
            assert result.ok is False and result.facts == {
                "status": "partial",
                "completed": 2 if scenario == "mixed_identity" else 1,
                "failed": 1,
            }
            assert _outcome(result) == "partial"
            assert "DO_NOT_ECHO" not in repr(result)
        else:
            assert _outcome(result) == ("success" if scenario == "success" else "cancelled")
    assert calls == [(identity, trigger) for identity in ("alice", "bob", "carol")]


@pytest.mark.parametrize("attempted,failed", [(True, 0), (2, True), (1, 2), (257, 1), (2, 0)])
def test_inconsistent_failed_receipt_never_attests_progress(attempted, failed):
    from cifereca.gui.source_inventory_timer import _scheduled_inventory_progress
    assert _scheduled_inventory_progress(SyncResult(ok=False, facts={
        "attempted": attempted, "failed": failed,
    })) == (False, True)
