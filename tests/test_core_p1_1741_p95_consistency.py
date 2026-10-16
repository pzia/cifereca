"""#1741: consistent, explicit p95 semantics for Core UI timing diagnostics."""
from __future__ import annotations

from cifereca.gui.ui_perf import UiPerfMonitor, _p95_nearest_rank_index


def test_p95_nearest_rank_does_not_underreport_tail_of_fourteen_samples():
    monitor = UiPerfMonitor(max_samples=20, slow_ms=1_000_000, measurement_origin="simulated")
    for number in range(1, 15):
        monitor.record("render", number * 10)
        assert monitor.record_phase("library", "render", number * 10)

    passive = monitor.snapshot()["render"]
    report = monitor.performance_report()
    detailed = report["stats"]["library.render"]

    assert passive["count"] == detailed["count"] == 14
    assert passive["p50_ms"] == detailed["p50_ms"] == 75
    assert passive["p95_ms"] == detailed["p95_ms"] == 140
    assert passive["max_ms"] == detailed["max_ms"] == 140
    assert report["percentile_method"] == "nearest_rank"
    assert report["measurement_origin"] == "simulated"
    assert report["events"] == []


def test_p95_rolling_window_is_bounded_and_same_in_both_reports():
    monitor = UiPerfMonitor(max_samples=20, slow_ms=1_000_000)
    for number in range(1, 22):
        monitor.record("render_library", number)
        monitor.record_phase("library", "row_projection", number)
    snapshots = monitor.snapshot()["render_library"]
    phases = monitor.performance_report()["stats"]["library.row_projection"]
    assert snapshots["count"] == phases["count"] == 20
    assert snapshots["p50_ms"] == phases["p50_ms"] == 11.5
    assert snapshots["p95_ms"] == phases["p95_ms"] == 20
    assert snapshots["max_ms"] == phases["max_ms"] == 21


def test_p95_index_matches_nearest_rank_for_sparse_bounded_samples():
    assert [_p95_nearest_rank_index(n) for n in (1, 2, 5, 14, 20, 100)] == [
        0, 1, 4, 13, 18, 94,
    ]
