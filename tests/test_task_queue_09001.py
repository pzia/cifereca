from __future__ import annotations

import threading
import time

from cifereca.task_queue import PriorityExecutor, TaskPriority
from cifereca.gui.ui_perf import UiPerfMonitor


def test_priority_executor_runs_queued_user_action_before_background_work():
    executor = PriorityExecutor(max_workers=1, thread_name_prefix="test-priority")
    blocker_started = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def blocker():
        blocker_started.set()
        release.wait(timeout=2)
        order.append("blocker")

    first = executor.submit(TaskPriority.NORMAL, blocker)
    assert blocker_started.wait(timeout=1)
    low = executor.submit(TaskPriority.MAINTENANCE, lambda: order.append("maintenance"))
    high = executor.submit(TaskPriority.USER_ACTION, lambda: order.append("user"))
    release.set()
    first.result(timeout=2)
    high.result(timeout=2)
    low.result(timeout=2)
    executor.shutdown(wait=True)
    assert order == ["blocker", "user", "maintenance"]


def test_priority_executor_cancels_pending_work_on_shutdown():
    executor = PriorityExecutor(max_workers=1, thread_name_prefix="test-cancel")
    started = threading.Event()
    release = threading.Event()

    def blocker():
        started.set()
        release.wait(timeout=2)

    running = executor.submit(TaskPriority.NORMAL, blocker)
    assert started.wait(timeout=1)
    pending = executor.submit(TaskPriority.MAINTENANCE, lambda: None)
    executor.shutdown(wait=False, cancel_futures=True)
    release.set()
    running.result(timeout=2)
    assert pending.cancelled()


def test_ui_perf_monitor_reports_rolling_percentiles():
    monitor = UiPerfMonitor(max_samples=20, slow_ms=10_000)
    for value in [1, 2, 3, 4, 100]:
        monitor.record("render", value)
    snapshot = monitor.snapshot()["render"]
    assert snapshot["count"] == 5
    assert snapshot["p50_ms"] == 3
    assert snapshot["p95_ms"] == 100
    assert snapshot["max_ms"] == 100
