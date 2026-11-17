"""Benchmark contract: assert counts and shape, not CI machine latency."""
import runpy
from pathlib import Path

import pytest

benchmark = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/benchmark_notice_c_journal.py")
)["benchmark"]


def test_small_scratch_journal_benchmark():
    report = benchmark(chains=2, samples=2)
    assert report["measurement_origin"] == "synthetic_scratch_sqlite"
    assert report["initial_jobs"] == 8
    assert report["latency"]["request_new"]["count"] == 8
    assert report["latency"]["request_dedup"]["count"] == 8
    for kind in ("fetch_source_notice", "reparse_cached_input",
                 "relink_notices", "recompute_aggregate"):
        for action in ("claim", "complete", "read"):
            assert report["latency"][action + "_" + kind]["count"] == 2
    for distribution in report["latency"].values():
        assert 0 <= distribution["p50_us"] <= distribution["p95_us"] <= distribution["max_us"]
    if report["counts"]["scan_port_available"]:
        assert report["latency"]["ready_page32"]["count"] == 2
        assert report["counts"]["rotation_examined"] <= 64
    if report["counts"]["filtered_scan_port_available"]:
        assert report["latency"]["four_filtered_pages8"]["count"] == 2
    else:
        assert "four_filtered_pages8" not in report["latency"]
    if report["counts"]["retention_port_available"]:
        assert 0 <= report["counts"]["recycled"] <= 8
    assert "sqlite" not in str(report["latency"])


@pytest.mark.parametrize("args", (
    {"chains": 513}, {"chains": 0}, {"chains": True}, {"samples": 129},
))
def test_benchmark_budget(args):
    with pytest.raises(ValueError, match="benchmark_sample_budget_invalid"):
        benchmark(**args)
