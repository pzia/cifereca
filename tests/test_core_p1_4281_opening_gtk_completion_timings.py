"""#4281: opening Sync GTK delivery cost must be distinct from provider I/O.

Only the callback's dashboard model and loan-list projection are timed.
Library/Recent are asynchronously queued and not included in these samples.
All work is synthetic: no real GTK, account, Paris/BnF request or data leak.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.ui_perf import UiPerfMonitor
from tests.test_core_ui_1132_opening_sync_close_gate import _host, _method


@pytest.mark.parametrize("deep,manual,expected_trigger", [
    (False, False, "automatic"),
    (True, False, "maintenance"),
    (False, True, "user"),
])
def test_opening_success_records_exact_gtk_phases_without_worker_or_async_work(
    deep, manual, expected_trigger, monkeypatch,
):
    start, queued = _method()
    host, tasks, effects = _host()
    monitor = UiPerfMonitor(measurement_origin="simulated")
    host.ui_perf = monitor
    monitor.enable_detailed_capture(True)
    # These are exact in-memory instants, not Paris/BnF response times.
    ticks = iter((10.0, 10.012, 10.012, 10.037))
    monkeypatch.setattr("time.perf_counter", lambda: next(ticks))
    control = object() if manual else None
    start(host, control=control, deep=deep)
    assert len(tasks) == 1
    args, _kwargs = tasks[0]
    args[2]({"identities": []})
    report = monitor.performance_report()
    phases = report["stats"]
    assert phases["opening-sync.model_update"]["p50_ms"] == pytest.approx(12.0)
    assert phases["opening-sync.render"]["p50_ms"] == pytest.approx(25.0)
    assert all(key.startswith("opening-sync.") for key in phases)
    assert [
        (event["category"], event["phase"], event["trigger"])
        for event in report["events"]
    ] == [
        ("opening-sync", "model_update", expected_trigger),
        ("opening-sync", "render", expected_trigger),
    ]
    assert [item[0] for item in effects] == [
        "dashboard", "loans", "library", "recent",
    ]
    assert queued == []


def test_failed_perf_recorder_cannot_interrupt_gtk_completion():
    start, _queued = _method()
    host, tasks, effects = _host()
    recorder = Mock(side_effect=RuntimeError("synthetic broken perf probe"))
    host.ui_perf = NS(record_phase=recorder)
    start(host)
    tasks[0][0][2]({"identities": []})
    assert recorder.call_count == 2
    assert [call.args[:2] for call in recorder.call_args_list] == [
        ("opening-sync", "model_update"), ("opening-sync", "render"),
    ]
    assert [item[0] for item in effects] == [
        "dashboard", "loans", "library", "recent",
    ]


def test_closed_owner_emits_no_gtk_timing_sample():
    start, _queued = _method()
    host, tasks, effects = _host()
    record = Mock()
    host.ui_perf = NS(record_phase=record)
    start(host)
    host._closed = True
    tasks[0][0][2]({"identities": []})
    record.assert_not_called()
    assert effects == []
