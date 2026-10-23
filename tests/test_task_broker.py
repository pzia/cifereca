from __future__ import annotations

import threading
import time

import pytest

from cifereca.plugin_api import Cancelled, TaskBroker, TaskHandle
from cifereca.task_runtime import TaskBrokerImpl


class _Service:
    pass


def test_task_broker_reports_progress_and_returns_public_handle():
    broker = TaskBrokerImpl(_Service(), max_workers=1)
    try:
        progress = []
        scoped = broker.for_plugin("plugin.test")
        assert isinstance(scoped, TaskBroker)

        def operation(control):
            control.report(current=1, total=2, message="phase 1")
            return "ok"

        handle = scoped.submit(
            operation,
            label="Test",
            on_progress=lambda current, total, message: progress.append((current, total, message)),
        )
        assert isinstance(handle, TaskHandle)
        assert handle.result(timeout=1) == "ok"
        assert handle.done()
        assert progress == [(1, 2, "phase 1")]
    finally:
        broker.close()


def test_task_broker_cancel_is_cooperative_for_running_work():
    broker = TaskBrokerImpl(_Service(), max_workers=1)
    started = threading.Event()
    try:
        def operation(control):
            started.set()
            while True:
                time.sleep(0.005)
                control.check_cancelled()

        handle = broker.for_plugin("plugin.test").submit(operation)
        assert started.wait(1)
        handle.cancel()
        with pytest.raises(Cancelled):
            handle.result(timeout=1)
    finally:
        broker.close()


def test_task_broker_close_rejects_new_work():
    broker = TaskBrokerImpl(_Service(), max_workers=1)
    scoped = broker.for_plugin("plugin.test")
    broker.close()
    with pytest.raises(RuntimeError, match="closed"):
        scoped.submit(lambda _control: None)


def test_optional_broker_pending_cap_rejects_without_leaking_reservation():
    broker = TaskBrokerImpl(_Service(), max_workers=1, max_pending=1)
    entered, release = threading.Event(), threading.Event()
    executed = []

    def blocking(_control):
        entered.set()
        assert release.wait(3)
        return "first"

    try:
        source = broker.for_plugin("private-origin")
        running = source.submit(blocking, label="PRIVATE_SIGNED_URL")
        assert entered.wait(2)
        pending = source.submit(lambda _: executed.append("pending") or "second")
        with pytest.raises(RuntimeError, match="executor pending queue full"):
            source.submit(lambda _: executed.append("rejected"), label="PRIVATE_TOKEN")
        # Broker reservations are cleaned even when admission rejects.
        assert len(broker._controls) == 2
        assert broker._executor.admission_snapshot()["rejected"] == 1
        release.set()
        assert running.result(timeout=3) == "first"
        assert pending.result(timeout=3) == "second"
        assert executed == ["pending"]
    finally:
        release.set()
        broker.close()


@pytest.mark.parametrize("priority", (
    True, False, 0.5, "0", None, object(),
))
def test_plugin_invalid_priority_rejected_before_broker_work(priority):
    from cifereca.task_admission import SharedTaskAdmission

    admission = SharedTaskAdmission(max_inflight=1, reserve_user_slots=0)
    broker = TaskBrokerImpl(
        _Service(), max_workers=1,
        shared_admission=admission, admission_scope="PRIVATE_PLUGIN_SCOPE",
    )
    try:
        with pytest.raises(ValueError, match="invalid task priority"):
            broker.for_plugin("PRIVATE_PLUGIN").submit(
                lambda control: pytest.fail("invalid priority executed"),
                priority=priority, label="PRIVATE_ACCOUNT",
            )
        assert broker._executor is None
        assert broker._controls == {}
        assert admission.snapshot()["inflight"] == 0
        assert admission.snapshot()["rejected"] == 0
        assert broker.for_plugin("valid").submit(
            lambda control: "accepted", priority=0,
        ).result(timeout=3) == "accepted"
    finally:
        broker.close()
    assert admission.snapshot()["inflight"] == 0


def test_plugin_priority_custom_int_not_invoked_under_broker_or_pool_lock():
    class HostilePriority:
        def __int__(self):
            raise AssertionError("PRIVATE_INT_HOOK_USED")

    broker = TaskBrokerImpl(_Service(), max_workers=1)
    try:
        with pytest.raises(ValueError, match="invalid task priority"):
            broker.submit(lambda control: None, priority=HostilePriority())
        assert broker._executor is None
        assert broker.submit(lambda control: "ok", priority=40).result(timeout=3) == "ok"
    finally:
        broker.close()
