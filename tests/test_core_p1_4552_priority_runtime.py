"""Q11: the existing runtime forwards a replaceable owner snapshot."""
from contextlib import closing
from threading import Event
from time import monotonic

import pytest

from cifereca.core.notice_c_work_runtime import NoticeWorkRuntime
from cifereca.storage import Storage
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl
from tests.test_core_p0_4982_dispatch_rotation import row, KINDS


def test_runtime_forwards_latest_hints_without_creating_work(tmp_path, monkeypatch):
    with closing(Storage(tmp_path / "priority.sqlite")) as storage:
        budget = SharedTaskAdmission(max_inflight=4, reserve_user_slots=1)
        broker = TaskBrokerImpl(object(), max_workers=3, shared_admission=budget)
        runtime = NoticeWorkRuntime(storage, broker, clock=lambda: 100)
        _, first = row(KINDS[3], 1)
        _, second = row(KINDS[3], 2)
        seen = []
        tick = runtime._pump.tick

        def observe(*, control=None, priority_hints=()):
            seen.append(priority_hints)
            return tick(control=control, priority_hints=priority_hints)

        monkeypatch.setattr(runtime._pump, "tick", observe)

        def scan():
            handle = runtime.request_tick()
            assert handle is not None
            outcome = handle.result(timeout=10)
            assert outcome.handles == ()
            deadline = monotonic() + 10
            while (runtime._scan is not None or budget.snapshot()["inflight"]) and monotonic() < deadline:
                Event().wait(.005)
            assert runtime._scan is None and not budget.snapshot()["inflight"]

        try:
            assert runtime.replace_priority_hints(((first, "interactive_visible"),))
            assert runtime.replace_priority_hints(((second, "recent_acquisition"),))
            # Rejected snapshots leave the last valid one intact.
            with pytest.raises(ValueError, match="invalid_priority_hints"):
                runtime.replace_priority_hints(((first, "invalid"),))
            assert runtime.progress_snapshot().scans_completed == 0
            scan()
            assert seen == [((second, "recent_acquisition"),)]
            assert runtime.replace_priority_hints(())
            scan()
            assert seen[-1] == ()
            assert storage.entitlements() == storage.acquisitions() == []
            runtime.close()
            assert not runtime.replace_priority_hints(((first, "interactive_visible"),))
        finally:
            pool = broker._executor
            runtime.close()
            broker.close()
            if pool is not None:
                pool.shutdown(wait=True, cancel_futures=True)
