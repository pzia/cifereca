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
                                    {"jobs_per_pool": 129}, {"io_ms": 11},
                                    {"coordinator_policy": "unknown"}])
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


def test_fair_coordinator_rotates_sources_and_never_discards_a_deferred_job():
    backlog = bench._FairBacklog(32)
    assert len(backlog) == 96
    first = [backlog.pop_next() for _ in range(4)]
    assert first == [(0, 0), (1, 0), (2, 0), (0, 40)]
    backlog.defer(*first[-1])  # A shared-scope refusal stays in the backlog.
    assert len(backlog) == 93
    remainder = []
    while backlog:
        remainder.append(backlog.pop_next())
    observed = first[:3] + remainder
    assert len(observed) == 96
    assert len(set(observed)) <= 6  # Only 3 synthetic pools and 2 classes.
    for pool in range(3):
        assert observed.count((pool, 0)) == 8
        assert observed.count((pool, 40)) == 24


@pytest.mark.parametrize("jobs", [1, 17, 32])
def test_fair_retry_policy_completes_and_releases_without_network(jobs):
    report = bench.benchmark(
        rounds=1, jobs_per_pool=jobs, io_ms=1,
        coordinator_policy="fair_round_robin",
    )
    assert report["schema_version"] == 2
    assert report["method"]["shared_coordinator_policy"] == "fair_round_robin"
    assert report["limits"]["continuous_arrival_fairness_not_proven"] is True
    for row in report["samples"]:
        assert row["completed"] == 3 * jobs
        assert row["end_to_end"]["user"]["samples"] + row["end_to_end"]["background"]["samples"] == 3 * jobs
        if row["mode"] == "shared_opt_in":
            assert row["coordinator_policy"] == "fair_round_robin"
            assert row["final_budget"]["inflight"] == 0
            assert row["final_budget"]["active_scopes"] == 0
            assert row["final_budget"]["admitted"] == 3 * jobs
            assert row["final_budget"]["rejected"] == row["rejected_attempts_retried_by_coordinator"]
        else:
            assert row["coordinator_policy"] == "not_applicable"
            assert row["rejected_attempts_retried_by_coordinator"] == 0
    json.dumps(report, allow_nan=False)
