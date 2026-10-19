"""P0 #1741: performance measurement is an observer, not a plugin call gate.

Exact numerical observations are preserved; arbitrary provider/user objects
must never be coerced, compared, retained or exported by this monitor.
No GTK, profile, network, filesystem, logging secret or remote telemetry.
"""
from __future__ import annotations

import json

import pytest

from cifereca.gui.ui_perf import UiPerfMonitor


PRIVATE = "https://private.invalid/profiles?token=PRIVATE_PERF_LABEL"


class Hostile:
    def __float__(self):
        raise AssertionError("Never call provider __float__ from performance probe")

    def __str__(self):
        raise AssertionError("Never stringify private performance input")

    def __eq__(self, _other):
        raise AssertionError("Never compare performance input to capture epoch")

    def __hash__(self):
        raise AssertionError("Never hash performance input")


@pytest.mark.parametrize("bad", [Hostile(), PRIVATE, None, True, [], {"ms": 4}])
def test_phase_sample_is_rejected_without_coercing_provider_payload(bad):
    monitor = UiPerfMonitor(measurement_origin="simulated")
    monitor.enable_detailed_capture(True)
    assert monitor.record_phase("task", "worker", bad) is False
    report = monitor.performance_report()
    assert report["no_samples"] is True
    assert report["events"] == []
    assert report["invalid_samples"] == 1
    assert "PRIVATE_PERF_LABEL" not in json.dumps(report)


@pytest.mark.parametrize("bad", [Hostile(), PRIVATE, True, {"duration": 3}])
def test_legacy_passive_record_does_not_invoke_private_float(bad):
    monitor = UiPerfMonitor()
    monitor.record("render_library", bad)
    assert monitor.snapshot() == {}
    assert monitor.performance_report()["invalid_samples"] == 1


@pytest.mark.parametrize("bad", [Hostile(), PRIVATE, True, {"timestamp": 3}])
def test_invalid_observed_timestamp_falls_back_to_capture_clock(bad):
    clock = [50.0]
    monitor = UiPerfMonitor(clock=lambda: clock[0], measurement_origin="simulated")
    monitor.enable_detailed_capture(True)
    clock[0] += 0.025
    assert monitor.record_phase(
        "source-materialize", "worker", 12.5, observed_at_ms=bad,
    )
    report = monitor.performance_report()
    assert report["events"][0]["at_ms"] == 25.0
    assert report["events"][0]["elapsed_ms"] == 12.5
    assert "PRIVATE_PERF_LABEL" not in json.dumps(report)


def test_invalid_epoch_never_reenters_detailed_capture_or_breaks_stats():
    monitor = UiPerfMonitor()
    monitor.enable_detailed_capture(True)
    assert monitor.record_phase(
        "task", "worker", 1, capture_generation=Hostile(),
    )
    assert monitor.performance_report()["events"] == []
    assert monitor.performance_report()["stats"]["task.worker"]["count"] == 1
    generation = monitor.capture_generation
    assert monitor.record_phase(
        "task", "worker", 2, capture_generation=generation,
    )
    assert [row["elapsed_ms"] for row in monitor.performance_report()["events"]] == [2.0]
    assert "PRIVATE_PERF_LABEL" not in json.dumps(monitor.performance_report())


@pytest.mark.parametrize("origin", [Hostile(), {"private": PRIVATE}, [PRIVATE], True])
def test_untrusted_monitor_provenance_cannot_crash_constructor_or_export(origin):
    monitor = UiPerfMonitor(measurement_origin=origin)
    monitor.record_phase("task", "worker", 3)
    report = monitor.performance_report()
    assert report["measurement_origin"] == "unknown"
    assert report["stats"]["task.worker"]["count"] == 1
    assert "PRIVATE_PERF_LABEL" not in json.dumps(report)
