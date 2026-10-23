"""#1741: a synthetic performance report must not overstate its evidence."""
from __future__ import annotations

from cifereca.gui.ui_perf import UiPerfMonitor


def test_sparse_mixed_tasks_keep_descriptive_p95_and_flag_insufficient_coverage():
    perf = UiPerfMonitor(measurement_origin="simulated")
    # Nine heterogeneous timings, including one extreme worker, deliberately
    # reproduce the misleading small-sample "other.worker" report.
    for duration in [1, 2, 3, 4, 5, 6, 7, 8, 233539]:
        assert perf.record_phase("other", "worker", duration)
    report = perf.performance_report()
    stats = report["stats"]["other.worker"]
    assert stats["count"] == 9
    assert stats["p95_ms"] == 233539
    assert report["evidence"]["phases"]["other.worker"]["sample_coverage"] == "limited"
    assert report["evidence"]["phases"]["other.worker"]["p95_interpretation"] == "sample_nearest_rank_only"
    assert report["measurement_origin"] == "simulated"
    assert report["events"] == []  # no private chronology without opt-in


def test_larger_bounded_series_never_claims_population_or_user_provenance():
    perf = UiPerfMonitor(measurement_origin="observed", max_samples=20)
    for duration in range(50):
        perf.record_phase("library", "render", duration)
    report = perf.performance_report()
    row = report["stats"]["library.render"]
    assert row["count"] == 20  # bounded rolling samples, not all 50
    assert report["evidence"]["phases"]["library.render"]["sample_coverage"] == "bounded_rolling"
    assert report["evidence"]["phases"]["library.render"]["p95_interpretation"] == "sample_nearest_rank_only"
    assert report["limits"]["max_samples_per_phase"] == 20


def test_resource_endpoints_never_masquerade_as_peak_or_continuous_series():
    readings = iter([
        {"cpu_ms": 100, "rss_bytes": 1000},
        {"cpu_ms": 150, "rss_bytes": 500},
    ])
    perf = UiPerfMonitor(
        clock=lambda: 100.0,
        resource_reader=lambda: next(readings),
    )
    assert perf.performance_report()["evidence"]["resources"]["sampling_strategy"] == "unavailable"
    perf.enable_detailed_capture(True)
    active = perf.performance_report()["resource_window"]
    assert active["sample_count"] == 1
    assert perf.performance_report()["evidence"]["resources"]["sampling_strategy"] == "start_only"
    assert perf.performance_report()["evidence"]["resources"]["peak_rss_bytes"] is None
    perf.enable_detailed_capture(False)
    stopped = perf.performance_report()["resource_window"]
    assert stopped["sample_count"] == 2
    assert perf.performance_report()["evidence"]["resources"]["sampling_strategy"] == "start_end_only"
    assert stopped["rss_start_bytes"] == 1000
    assert stopped["rss_end_bytes"] == 500
    assert stopped["peak_rss_measured"] is False
    assert perf.performance_report()["evidence"]["resources"]["peak_rss_bytes"] is None
