"""P1 #1741: bounded real task observations, no private labels or implicit capture."""
from __future__ import annotations

import ast
from concurrent.futures import Future
import itertools
import json
import logging
import math
from pathlib import Path
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.ui_perf import UiPerfMonitor
from cifereca.plugin_api import UserFacingError, AttentionRequired


def test_phase_aggregates_reuse_monitor_percentiles_and_mark_missing_data():
    clock = [10.0]
    mon = UiPerfMonitor(max_samples=20, clock=lambda: clock[0], slow_ms=10000)
    assert mon.performance_report()["no_samples"] is True
    for ms in (1, 2, 3, 4, 100):
        assert mon.record_phase("source-sync", "worker", ms, operation_id=9)
    report = mon.performance_report()
    assert report["schema"] == "cifereca.performance.phases/1"
    assert report["measurement_origin"] == "observed"
    assert report["no_samples"] is False
    assert report["events"] == []  # detailed capture is opt-in
    assert report["stats"]["source-sync.worker"] == {
        "count": 5, "p50_ms": 3, "p95_ms": 100, "max_ms": 100,
    }
    assert mon.snapshot() == {}  # existing UI surface is unchanged


def test_capture_timeline_has_only_ephemeral_numeric_task_ids():
    clock = [5.0]
    mon = UiPerfMonitor(
        max_events=2, clock=lambda: clock[0], measurement_origin="simulated",
    )
    mon.enable_detailed_capture(True)
    for index in range(1, 4):
        clock[0] += 0.1
        mon.record_phase(
            "https://PRIVATE_USER/private?token=SECRET", "queue_wait", index,
            operation_id=index, trigger="PRIVATE_PROVIDER", outcome="SECRET",
        )
    report = mon.performance_report()
    assert report["measurement_origin"] == "simulated"
    assert report["events_dropped"] == 1
    assert [row["operation_id"] for row in report["events"]] == [2, 3]
    assert [row["category"] for row in report["events"]] == ["other", "other"]
    assert [row["trigger"] for row in report["events"]] == [
        "unspecified", "unspecified",
    ]
    assert all(row["outcome"] == "unknown" for row in report["events"])
    dumped = json.dumps(report)
    for secret in ("PRIVATE", "SECRET", "token=", "https://"):
        assert secret not in dumped
    assert report["remote_upload"] is False
    mon.enable_detailed_capture(False)
    mon.record_phase("task", "worker", 4, operation_id=4)
    assert [row["operation_id"] for row in mon.performance_report()["events"]] == [2, 3]


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), "SECRET", None, True, 86_400_001])
def test_invalid_durations_are_rejected_not_exported(value):
    mon = UiPerfMonitor()
    assert mon.record_phase("task", "worker", value) is False
    assert mon.performance_report()["invalid_samples"] == 1
    assert mon.performance_report()["no_samples"] is True


def test_phases_fail_closed_and_boolean_task_ids_are_not_emitted():
    mon = UiPerfMonitor()
    mon.enable_detailed_capture(True)
    assert mon.record_phase("task", "https://private", 10) is False
    assert mon.record_phase("task", "worker", 10, operation_id=True)
    event = mon.performance_report()["events"][0]
    assert event["operation_id"] is None
    assert all(not key.startswith("https") for key in mon.performance_report()["stats"])


def test_capture_reset_is_explicit_and_bounds_do_not_grow():
    mon = UiPerfMonitor(max_samples=20, max_events=1)
    with pytest.raises(ValueError, match="invalid_performance_capture_option"):
        mon.enable_detailed_capture("yes")
    mon.enable_detailed_capture(True)
    for i in range(100):
        assert mon.record_phase("task", "worker", i, operation_id=i + 1)
    report = mon.performance_report()
    assert report["stats"]["task.worker"]["count"] == 20
    assert len(report["events"]) == 1
    assert report["events_dropped"] == 99
    mon.enable_detailed_capture(False)
    mon.enable_detailed_capture(True)
    assert mon.performance_report()["events"] == []
    assert mon.performance_report()["events_dropped"] == 0


def _headless_runner():
    """Compile the actual TaskRunner without importing GI or changing GTK."""
    source = (
        Path(__file__).resolve().parents[1]
        / "src/cifereca/gui/task_runner.py"
    ).read_text(encoding="utf-8")
    original = ast.parse(source)
    selected = [
        node for node in original.body
        if isinstance(node, ast.ClassDef) and node.name == "TaskRunner"
    ]
    assert len(selected) == 1
    tree = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__",
                       names=[ast.alias(name="annotations")], level=0),
        *selected,
    ], type_ignores=[]))

    class Pool:
        def __init__(self, **kwargs):
            pass

        def submit(self, priority, fn):
            future = Future()
            try:
                future.set_result(fn())
            except BaseException as exc:
                future.set_exception(exc)
            return future

        def shutdown(self, **kwargs):
            pass

    callbacks = []
    glib = NS(idle_add=lambda fn, *args: callbacks.append((fn, args)))
    env = dict(
        Any=object, Callable=object,
        Gtk=NS(Widget=object),
        itertools=itertools, threading=threading, time=time,
        Future=Future, PriorityExecutor=Pool,
        TaskPriority=NS(NORMAL=2), GLib=glib,
        _TRACE_LOG=logging.getLogger("test-core-perf"),
        UserFacingError=UserFacingError, AttentionRequired=AttentionRequired,
    )
    exec(compile(tree, "task_runner.py", "exec"), env)

    def flush():
        while callbacks:
            fn, args = callbacks.pop(0)
            fn(*args)

    return env["TaskRunner"], flush


def test_actual_task_runner_observes_three_correlated_phases_without_private_labels():
    TaskRunner, flush = _headless_runner()
    mon = UiPerfMonitor(max_samples=20)
    mon.enable_detailed_capture(True)
    history = []
    window = NS(
        _closed=False, ui_perf=mon,
        _task_started=lambda *args: history.append(("started", args)),
        _task_finished=lambda *args: history.append(("finished", args)),
        _task_progress_for_task=lambda *args: None,
    )
    runner = TaskRunner(window)
    runner.submit(
        "https://PRIVATE/?secret=SECRET", lambda: 7,
        ok=lambda result: history.append(("success", result)),
        kind="source-sync", origin="user",
    )
    flush()
    report = mon.performance_report()
    assert [event["phase"] for event in report["events"]] == [
        "queue_wait", "worker", "gtk_delivery_wait",
    ]
    assert {event["operation_id"] for event in report["events"]} == {1}
    assert {event["category"] for event in report["events"]} == {"source-sync"}
    assert {event["trigger"] for event in report["events"]} == {"user"}
    assert all(event["elapsed_ms"] >= 0 for event in report["events"])
    for private in ("PRIVATE", "SECRET", "https://"):
        assert private not in json.dumps(report)
    assert history[-1] == ("success", 7)
    runner.close()


def test_actual_task_runner_keeps_failed_worker_outcome_without_payload_in_report():
    TaskRunner, flush = _headless_runner()
    mon = UiPerfMonitor()
    mon.enable_detailed_capture(True)
    errors = []

    def fail():
        raise ValueError("PRIVATE_TOKEN")

    window = NS(
        _closed=False, ui_perf=mon,
        _task_started=Mock(), _task_finished=Mock(),
        _task_progress_for_task=Mock(),
    )
    runner = TaskRunner(window)
    runner.submit("Private title", fail, err=errors.append,
                  kind="kobo-acquisition", origin="automatic")
    flush()
    report = mon.performance_report()
    assert len(errors) == 1
    assert [e["outcome"] for e in report["events"]] == [
        "unknown", "failed", "failed",
    ]
    assert "PRIVATE_TOKEN" not in json.dumps(report)
    assert report["stats"]["kobo-acquisition.worker"]["count"] == 1
    runner.close()


def test_duplicate_automatic_task_does_not_create_phantom_timing_samples():
    TaskRunner, flush = _headless_runner()
    mon = UiPerfMonitor(measurement_origin="simulated")
    mon.enable_detailed_capture(True)
    window = NS(
        _closed=False, ui_perf=mon,
        _task_started=Mock(), _task_finished=Mock(),
        _task_progress_for_task=Mock(),
    )
    runner = TaskRunner(window)
    work = Mock(return_value={"status": "ok"})
    runner.submit(
        "Ouverture", work, kind="opening-sync",
        coalesce_key="identical-passive-request", origin="automatic",
    )
    # The worker Future has completed but the GTK delivery is still queued.
    # The second automatic submit must observe the same in-flight work.
    runner.submit(
        "Ouverture identique", work, kind="opening-sync",
        coalesce_key="identical-passive-request", origin="automatic",
    )
    assert work.call_count == 1
    flush()
    events = mon.performance_report()["events"]
    assert [event["phase"] for event in events] == [
        "queue_wait", "worker", "gtk_delivery_wait",
    ]
    assert {event["operation_id"] for event in events} == {1}
    assert mon.performance_report()["stats"]["opening-sync.worker"]["count"] == 1
    runner.close()


def test_closed_runner_never_generates_task_samples():
    TaskRunner, flush = _headless_runner()
    mon = UiPerfMonitor(measurement_origin="simulated")
    window = NS(_closed=True, ui_perf=mon,
                _task_started=Mock(), _task_finished=Mock())
    runner = TaskRunner(window)
    worker = Mock()
    runner.submit("No work", worker, kind="source-sync")
    assert mon.performance_report()["no_samples"] is True
    worker.assert_not_called()
    flush()
    runner.close()


@pytest.mark.parametrize("kind", (
    "maintenance-plan", "maintenance-tool", "maintenance-inspection",
))
def test_real_maintenance_task_has_own_phase_category_in_opt_in_export(kind):
    """The known audit phase must not collapse to 'other.worker' (#1741)."""
    TaskRunner, flush = _headless_runner()
    monitor = UiPerfMonitor(measurement_origin="simulated")
    monitor.enable_detailed_capture(True)
    window = NS(
        _closed=False, ui_perf=monitor,
        _task_started=Mock(), _task_finished=Mock(),
        _task_progress_for_task=Mock(),
    )
    runner = TaskRunner(window)
    try:
        runner.submit(
            "https://private.invalid/audit?token=PRIVATE_AUDIT",
            lambda: "synthetic-audit-complete",
            kind=kind, origin="maintenance",
        )
        flush()
        report = monitor.performance_report()
        assert set(report["stats"]) == {
            f"{kind}.queue_wait", f"{kind}.worker",
            f"{kind}.gtk_delivery_wait",
        }
        assert len(report["events"]) == 3
        assert {event["category"] for event in report["events"]} == {kind}
        assert {event["operation_id"] for event in report["events"]} == {1}
        assert "other.worker" not in report["stats"]
        assert "PRIVATE_AUDIT" not in json.dumps(report)
        assert report["measurement_origin"] == "simulated"
    finally:
        runner.close()


def test_phase_export_remains_closed_for_arbitrary_kind_after_maintenance_fix():
    monitor = UiPerfMonitor(measurement_origin="simulated")
    monitor.enable_detailed_capture(True)
    assert monitor.record_phase("maintenance-tool", "worker", 10)
    assert monitor.record_phase("maintenance/SIGNED_PRIVATE_URL", "worker", 20)
    report = monitor.performance_report()
    assert set(report["stats"]) == {
        "maintenance-tool.worker", "other.worker",
    }
    assert [row["category"] for row in report["events"]] == [
        "maintenance-tool", "other",
    ]
    assert "SIGNED_PRIVATE_URL" not in json.dumps(report)
