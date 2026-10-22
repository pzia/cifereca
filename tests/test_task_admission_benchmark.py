"""Check load-tool accounting and provenance without timing thresholds."""
import json
import importlib.util
from pathlib import Path

import pytest

from scripts import benchmark_task_admission as bench


@pytest.mark.parametrize("jobs", [1, 17])
def test_real_pools_complete_identical_work_and_release_every_lease(jobs):
    report = bench.benchmark(rounds=1, jobs_per_pool=jobs, io_ms=1)
    assert report["measurement_origin"] == "simulated"
    assert report["performance_verdict"] == "not_qualified"
    assert [r["mode"] for r in report["samples"]] == ["shared_opt_in", "legacy_unbounded"]
    for row in report["samples"]:
        assert row["completed"] == jobs * 3
        assert sum(x["samples"] for x in row["end_to_end"].values()) == jobs * 3
        assert len(row["pools"]) == 3
        if row["final_budget"]:
            assert row["final_budget"]["inflight"] == row["final_budget"]["active_scopes"] == 0
            assert row["final_budget"]["admitted"] == jobs * 3
            assert row["final_budget"]["rejected"] == row["rejected_attempts_retried_by_coordinator"]
        else:
            assert row["rejected_attempts_retried_by_coordinator"] == 0
    assert report["limits"]["network_used"] is False
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("kwargs", [{"rounds": 0}, {"rounds": True},
                                    {"jobs_per_pool": 129}, {"io_ms": 11}])
def test_refuses_unbounded_or_invalid_workloads(kwargs):
    with pytest.raises(ValueError, match="benchmark_sample_budget_invalid"):
        bench.benchmark(**kwargs)


def test_offline_load_tool_needs_no_gtk_but_preserves_other_changed_paths():
    path = Path(__file__).resolve().parents[1] / "scripts/ci-select-e2e.py"
    spec = importlib.util.spec_from_file_location("admission_bench_e2e_selector", path)
    selector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selector)
    paths = ["scripts/benchmark_task_admission.py", "tests/test_task_admission_benchmark.py"]
    assert selector.select(paths) == ()
    assert selector.select(paths + ["src/cifereca/core/sync_coordination.py"]) == selector.SYNC_CORE
    assert selector.select(paths + ["src/cifereca/unknown.py"]) == selector.ALL
