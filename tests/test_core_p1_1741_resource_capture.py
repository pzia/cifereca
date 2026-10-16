"""P1 #1741: CPU/RSS are sampled only by explicit capture state changes.

Headless tests use deterministic clocks and reader doubles, never a profiler,
real provider, local account metadata, GTK or remote installation.
"""
from __future__ import annotations

import io
import json
from unittest.mock import Mock

import pytest

from cifereca.gui import ui_perf
from cifereca.gui.ui_perf import UiPerfMonitor


def _reader(*values):
    values = iter(values)
    reader = Mock(side_effect=lambda: next(values))
    return reader


def test_capture_resource_window_is_opt_in_and_does_not_invent_samples():
    clock = [10.0]
    reader = _reader(
        {"cpu_ms": 1000.0, "rss_bytes": 120 * 1024 * 1024},
        {"cpu_ms": 1150.0, "rss_bytes": 122 * 1024 * 1024},
    )
    monitor = UiPerfMonitor(clock=lambda: clock[0],
                            resource_reader=reader,
                            measurement_origin="simulated")
    for _ in range(5):
        assert monitor.performance_report()["resource_window"]["state"] == "not_started"
        monitor.record_phase("task", "worker", 1)
    reader.assert_not_called()
    monitor.enable_detailed_capture(True)
    monitor.enable_detailed_capture(True)  # no duplicate expensive sample
    started = monitor.performance_report()["resource_window"]
    assert started["state"] == "active" and started["sample_count"] == 1
    assert started["rss_start_bytes"] == 120 * 1024 * 1024
    assert started["rss_end_bytes"] is None
    assert started["process_cpu_ms"] is None
    assert started["process_cpu_percent_one_core"] is None
    assert reader.call_count == 1
    clock[0] = 10.5
    monitor.enable_detailed_capture(False)
    monitor.enable_detailed_capture(False)
    report = monitor.performance_report()
    resources = report["resource_window"]
    assert resources == {
        "state": "stopped", "sample_count": 2, "wall_ms": 500.0,
        "process_cpu_ms": 150.0, "process_cpu_percent_one_core": 30.0,
        "rss_start_bytes": 120 * 1024 * 1024,
        "rss_end_bytes": 122 * 1024 * 1024,
        "rss_source": "linux_proc_statm_current", "peak_rss_measured": False,
    }
    assert report["measurement_origin"] == "simulated"
    assert reader.call_count == 2
    assert report["remote_upload"] is False
    assert "frame_wait" not in report["stats"]


def test_restart_resets_window_and_multicore_cpu_can_exceed_100_percent():
    tick = [100.0]
    reader = _reader(
        {"cpu_ms": 1, "rss_bytes": None},
        {"cpu_ms": 3, "rss_bytes": None},
        {"cpu_ms": 10, "rss_bytes": None},
        {"cpu_ms": 610, "rss_bytes": None},
    )
    monitor = UiPerfMonitor(clock=lambda: tick[0], resource_reader=reader)
    monitor.enable_detailed_capture(True)
    tick[0] += 0.1
    monitor.enable_detailed_capture(False)
    assert monitor.performance_report()["resource_window"]["process_cpu_ms"] == 2
    monitor.enable_detailed_capture(True)
    assert monitor.performance_report()["resource_window"]["state"] == "active"
    assert monitor.performance_report()["resource_window"]["process_cpu_ms"] is None
    tick[0] += 0.2
    monitor.enable_detailed_capture(False)
    result = monitor.performance_report()["resource_window"]
    assert result["process_cpu_ms"] == 600
    assert result["process_cpu_percent_one_core"] == 300
    assert result["rss_start_bytes"] is None
    assert result["rss_end_bytes"] is None
    assert result["rss_source"] == "unavailable"
    assert result["sample_count"] == 2
    assert reader.call_count == 4


@pytest.mark.parametrize("invalid", [
    "https://private.invalid/?token=ACCOUNT_SECRET",
    {"cpu_ms": "Bearer ACCOUNT_SECRET", "rss_bytes": 9000},
    {"cpu_ms": float("nan"), "rss_bytes": 9000},
    {"cpu_ms": True, "rss_bytes": 9000},
    {"cpu_ms": -1, "rss_bytes": 9000},
    {"cpu_ms": float("inf"), "rss_bytes": 9000},
])
def test_invalid_resource_sample_is_rejected_before_retention(invalid):
    private = "ACCOUNT_SECRET"
    reader = _reader(invalid, {"cpu_ms": 100, "rss_bytes": "Bearer ACCOUNT_SECRET"})
    monitor = UiPerfMonitor(resource_reader=reader)
    monitor.enable_detailed_capture(True)
    monitor.enable_detailed_capture(False)
    result = monitor.performance_report()
    resources = result["resource_window"]
    assert resources["state"] == "stopped"
    assert resources["sample_count"] == 1
    assert resources["process_cpu_ms"] is None
    assert resources["rss_start_bytes"] is None
    assert resources["rss_end_bytes"] is None
    assert private not in json.dumps(result)
    assert "https://" not in json.dumps(result)


def test_reader_exception_is_swallowed_and_not_leaked():
    reader = Mock(side_effect=RuntimeError("TOKEN_PRIVATE_ACCOUNT"))
    monitor = UiPerfMonitor(resource_reader=reader)
    monitor.enable_detailed_capture(True)
    monitor.enable_detailed_capture(False)
    report = monitor.performance_report()
    assert report["resource_window"]["sample_count"] == 0
    assert report["resource_window"]["process_cpu_ms"] is None
    assert "TOKEN_PRIVATE_ACCOUNT" not in json.dumps(report)
    assert reader.call_count == 2


def test_linux_statm_reads_resident_pages_not_virtual_size(monkeypatch):
    seen = []
    def fake_open(path, *, encoding):
        seen.append((path, encoding))
        return io.StringIO("999999 321 0 0 0 0 0")
    monkeypatch.setattr(ui_perf.sys, "platform", "linux")
    monkeypatch.setattr(ui_perf, "open", fake_open, raising=False)
    monkeypatch.setattr(ui_perf.os, "sysconf", lambda name: 4096)
    monkeypatch.setattr(ui_perf.time, "process_time", lambda: 1.25)
    result = ui_perf._process_resource_sample()
    assert result == {"cpu_ms": 1250.0, "rss_bytes": 321 * 4096}
    assert seen == [("/proc/self/statm", "ascii")]


def test_unsupported_procfs_exposes_absent_rss_not_fake_zero(monkeypatch):
    monkeypatch.setattr(ui_perf.sys, "platform", "win32")
    monkeypatch.setattr(ui_perf, "open",
                        Mock(side_effect=AssertionError("must not read statm")),
                        raising=False)
    monkeypatch.setattr(ui_perf.time, "process_time", lambda: 2.0)
    assert ui_perf._process_resource_sample() == {
        "cpu_ms": 2000.0, "rss_bytes": None,
    }


def test_unavailable_statm_does_not_break_capture(monkeypatch):
    monkeypatch.setattr(ui_perf.sys, "platform", "linux")
    monkeypatch.setattr(ui_perf, "open",
                        Mock(side_effect=OSError("PRIVATE_PATH")),
                        raising=False)
    result = ui_perf._process_resource_sample()
    assert result["rss_bytes"] is None
    assert type(result["cpu_ms"]) is float
