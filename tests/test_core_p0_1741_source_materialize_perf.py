"""#1741/#2131: distinguish Numilog startup materialization from generic task timings.

The real TaskRunner.submit(kind="source-materialize") forwards that fixed kind
to UiPerfMonitor.record_phase. Only numeric timing and an ephemeral operation
sequence are accepted by the capture/export boundary.
"""
from __future__ import annotations

import json

from cifereca.gui.ui_perf import UiPerfMonitor


def test_source_materialize_has_distinct_passive_queue_and_worker_stats():
    monitor = UiPerfMonitor(measurement_origin="simulated")
    assert monitor.record_phase(
        "source-materialize", "queue_wait", 12, operation_id=7,
        trigger="automatic", outcome="unknown",
    )
    assert monitor.record_phase(
        "source-materialize", "worker", 1200, operation_id=7,
        trigger="automatic", outcome="failed",
    )
    report = monitor.performance_report()
    assert report["schema"] == "cifereca.performance.phases/1"
    assert report["measurement_origin"] == "simulated"
    assert report["events"] == []  # no opt-in, no chronology
    assert report["stats"]["source-materialize.queue_wait"]["count"] == 1
    assert report["stats"]["source-materialize.worker"]["p50_ms"] == 1200
    assert "other.worker" not in report["stats"]


def test_opt_in_capture_correlates_queue_worker_and_gtk_delivery_without_identifiers():
    monitor = UiPerfMonitor(measurement_origin="observed", max_events=3)
    monitor.enable_detailed_capture(True)
    assert monitor.record_phase(
        "source-materialize", "queue_wait", 15,
        operation_id=42, trigger="automatic", outcome="unknown",
    )
    assert monitor.record_phase(
        "source-materialize", "worker", 160,
        operation_id=42, trigger="automatic", outcome="failed",
    )
    assert monitor.record_phase(
        "source-materialize", "gtk_delivery_wait", 20,
        operation_id=42, trigger="automatic", outcome="failed",
    )
    monitor.enable_detailed_capture(False)
    report = monitor.performance_report()
    assert report["resource_window"]["state"] == "stopped"
    assert report["remote_upload"] is False
    assert [row["phase"] for row in report["events"]] == [
        "queue_wait", "worker", "gtk_delivery_wait",
    ]
    assert [row["category"] for row in report["events"]] == [
        "source-materialize",
    ] * 3
    assert {row["operation_id"] for row in report["events"]} == {42}
    assert {row["trigger"] for row in report["events"]} == {"automatic"}
    assert [row["outcome"] for row in report["events"]] == [
        "unknown", "failed", "failed",
    ]
    assert all(row["elapsed_ms"] >= 0 for row in report["events"])


def test_untrusted_kind_and_context_still_collapse_to_closed_public_values():
    monitor = UiPerfMonitor(max_events=2)
    monitor.enable_detailed_capture(True)
    secret = "https://private.invalid/reader?token=SECRET"
    assert monitor.record_phase(
        secret, "worker", 5, trigger=secret, outcome=secret,
        operation_id=secret,
    )
    assert monitor.record_phase(
        "source-materialize", "worker", 7,
        trigger="automatic", outcome="success", operation_id=14,
    )
    report = monitor.performance_report()
    assert [(row["category"], row["trigger"], row["outcome"]) for row in report["events"]] == [
        ("other", "unspecified", "unknown"),
        ("source-materialize", "automatic", "success"),
    ]
    assert report["stats"]["other.worker"]["count"] == 1
    assert report["stats"]["source-materialize.worker"]["count"] == 1
    encoded = json.dumps(report)
    assert "SECRET" not in encoded
    assert secret not in encoded


def test_materialization_stats_are_bounded_across_repeated_startup_tasks():
    monitor = UiPerfMonitor(max_samples=20, max_events=2)
    for i in range(100):
        assert monitor.record_phase(
            "source-materialize", "worker", i + 1,
            trigger="automatic", outcome="success",
        )
    stats = monitor.performance_report()["stats"]["source-materialize.worker"]
    assert stats == {"count": 20, "p50_ms": 90.5, "p95_ms": 99, "max_ms": 100}
    assert monitor.performance_report()["events"] == []
