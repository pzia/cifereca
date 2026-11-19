"""#6936: no timing threshold or optimization authority in CI."""
from pathlib import Path
import runpy

import pytest

benchmark = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/benchmark_physical_sha.py")
)["benchmark"]


def test_guarded_scratch_benchmark_counts_and_latency_shape():
    report = benchmark(files=2, size_mib=1, passes=2)
    assert report["measurement_origin"] == "synthetic_scratch_warm_cache"
    assert report["production_optimization_applied"] is False
    assert report["rights_or_placement_authorized"] is False
    cases = report["baseline"] + report["candidates"]
    assert [(c["reader"], c["workers"]) for c in cases] == [
        ("catalog", 1), ("pinned", 1), ("pinned", 2), ("pinned", 4),
    ]
    for c in cases:
        assert c["files_checked"] == c["file_latency"]["count"] == 4
        assert c["batch_latency"]["count"] == 2
        assert c["logical_bytes_hashed"] == 4 * 1024 * 1024
        for key in ("file_latency", "batch_latency"):
            d = c[key]
            assert 0 <= d["p50_ms"] <= d["p95_ms"] <= d["max_ms"]
        assert c["wall_ms"] >= 0 and c["cpu_ms"] >= 0
        assert c["process_peak_rss_kib_cumulative"] > 0
        if c["proc_io_delta"] is not None:
            assert c["proc_io_delta"]["rchar"] >= c["logical_bytes_hashed"]
            assert c["proc_io_delta"]["read_bytes"] >= 0


@pytest.mark.parametrize("options", [
    {"files": True}, {"files": 1}, {"size_mib": 0}, {"files": 512, "size_mib": 2},
    {"passes": 13}, {"passes": 1.0},
])
def test_refuses_oversized_or_invalid_campaign(options):
    with pytest.raises(ValueError, match="benchmark_budget_invalid"):
        benchmark(**options)


def test_readers_reject_replaced_bytes_and_pinned_ancestor_symlink(tmp_path):
    ns = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                            "scripts/benchmark_physical_sha.py"))
    root = tmp_path / "owner"
    root.mkdir()
    job, = ns["_corpus"](root, 1, 1024)
    path, digest, size = job
    other = tmp_path / "other"
    other.mkdir()
    (other / path.name).write_bytes(path.read_bytes())
    root.rename(tmp_path / "old")
    root.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError):
        ns["_read"](job, "pinned")
    # Same-length alteration, no metadata shortcut.
    path.unlink()
    path.write_bytes(b"x" * size)
    with pytest.raises(RuntimeError, match="benchmark_hash_mismatch"):
        ns["_read"](job, "catalog")
