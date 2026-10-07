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
