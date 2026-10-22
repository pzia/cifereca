"""Offline comparison of 3/3/8 PriorityExecutor pools (#2431).

Pool sizes model GTK/Plugins/Search; these integrations are NOT executed.
Synthetic waits use no network, provider, configuration or private data.
"""
from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import FIRST_COMPLETED, wait
import json
import math
import os
import platform
import sys
import threading
import time

from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_queue import PriorityExecutor


def _distribution(values):
    ordered = sorted(values)
    return {"samples": len(ordered), **{
        f"p{p}_ms": round(ordered[math.ceil(len(ordered) * p / 100) - 1], 3)
        if ordered else None for p in (50, 95)}}


def _rss_bytes():
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/statm", encoding="ascii") as handle:
                return int(handle.read(128).split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            pass
    return None


class _FairBacklog:
    """Bounded coordinator-owned queue, no retries inside executor workers.

    Three user submissions precede one background submission while both are
    available; candidate pools rotate. This is a finite synthetic policy, not
    a production fairness guarantee under continual arrivals.
    """

    def __init__(self, jobs_per_pool):
        self._queues = [
            {
                0: deque(0 for job in range(jobs_per_pool) if job % 4 == 0),
                40: deque(40 for job in range(jobs_per_pool) if job % 4 != 0),
            }
            for _ in range(3)
        ]
        self._size = jobs_per_pool * 3
        self._cursor = 0
        self._user_streak = 0

    def __len__(self):
        return self._size

    def pop_next(self):
        preferred = 40 if self._user_streak >= 3 else 0
        for priority in (preferred, 0 if preferred == 40 else 40):
            for offset in range(3):
                pool = (self._cursor + offset) % 3
                if self._queues[pool][priority]:
                    self._queues[pool][priority].popleft()
                    self._size -= 1
                    self._cursor = (pool + 1) % 3
                    self._user_streak = (
                        0 if priority == 40 else min(3, self._user_streak + 1)
                    )
                    return pool, priority
        raise IndexError("coordinator backlog empty")

    def defer(self, pool, priority):
        self._queues[pool][priority].appendleft(priority)
        self._size += 1


def _run_case(shared, jobs_per_pool, io_ms, *, coordinator_policy="legacy_scan"):
    budget = SharedTaskAdmission(max_inflight=12, reserve_user_slots=2, max_per_scope=4) if shared else None
    pools = [PriorityExecutor(max_workers=n, shared_admission=budget,
                              admission_scope=f"synthetic-{i}" if shared else None)
             for i, n in enumerate((3, 3, 8))]
    # All requests exist at t0. Refused work stays with THIS coordinator;
    # no quota-owning worker waits for another task's admission.
    backlog = (
        _FairBacklog(jobs_per_pool)
        if shared and coordinator_policy == "fair_round_robin"
        else [(pool, 0 if job % 4 == 0 else 40)
              for job in range(jobs_per_pool) for pool in range(3)]
    )
    active, latency = {}, {"user": [], "background": []}
    rejected, peak = 0, [0, 0, 0]
    gate = threading.Event()
    rss_start, cpu_start, started = _rss_bytes(), time.process_time(), time.perf_counter()

    def work():
        gate.wait(io_ms / 1000)
        return (time.perf_counter() - started) * 1000

    try:
        while backlog or active:
            if time.perf_counter() - started > 15:
                raise RuntimeError("synthetic_workload_deadline_exceeded")
            if isinstance(backlog, _FairBacklog):
                # Only probe for slots that are currently available. Unlike
                # scanning the entire backlog, do not flood the shared budget
                # with retries while workers are busy. Scope limits can still
                # reject candidates; those are deferred outside all pools.
                free = max(0, budget.max_inflight - budget.snapshot()["inflight"])
                candidates = [backlog.pop_next() for _ in range(min(len(backlog), free))]
            else:
                candidates, backlog = backlog, []
            for pool, priority in candidates:
                try:
                    future = pools[pool].submit(priority, work)
                except RuntimeError as exc:
                    if str(exc) != "shared task admission full":
                        raise
                    rejected += 1
                    if isinstance(backlog, _FairBacklog):
                        backlog.defer(pool, priority)
                    else:
                        backlog.append((pool, priority))
                else:
                    active[future] = "user" if priority == 0 else "background"
                peak[pool] = max(peak[pool], pools[pool].admission_snapshot()["pending"])
            if active:
                completed, _ = wait(active, timeout=.05, return_when=FIRST_COMPLETED)
                for future in completed:
                    latency[active.pop(future)].append(future.result())
            elif backlog:
                # Completion precedes the worker's final lease release.
                gate.wait(.001)  # avoid a coordinator busy-spin
        elapsed, cpu_ms = (time.perf_counter() - started) * 1000, (time.process_time() - cpu_start) * 1000
    finally:
        for pool in pools:
            pool.shutdown(wait=True)
    final_budget = budget.snapshot() if budget else None
    count = sum(map(len, latency.values()))
    if count != jobs_per_pool * 3 or (budget and final_budget["inflight"] != 0):
        raise RuntimeError("synthetic_workload_accounting_failed")
    return {
        "mode": "shared_opt_in" if shared else "legacy_unbounded", "completed": count,
        "coordinator_policy": coordinator_policy if shared else "not_applicable",
        "rejected_attempts_retried_by_coordinator": rejected,
        "elapsed_ms": round(elapsed, 3), "process_cpu_ms": round(cpu_ms, 3),
        "rss_before_bytes": rss_start, "rss_after_bytes": _rss_bytes(),
        "end_to_end": {name: _distribution(values) for name, values in latency.items()},
        "sampled_peak_pending_by_pool": peak,
        "pools": [pool.timing_snapshot() for pool in pools], "final_budget": final_budget,
    }


def benchmark(*, rounds=5, jobs_per_pool=64, io_ms=2,
              coordinator_policy="legacy_scan"):
    if (type(rounds) is not int or not 1 <= rounds <= 7
            or type(jobs_per_pool) is not int or not 1 <= jobs_per_pool <= 128
            or type(io_ms) is not int or not 0 <= io_ms <= 10
            or coordinator_policy not in ("legacy_scan", "fair_round_robin")):
        raise ValueError("benchmark_sample_budget_invalid")
    samples = []
    for index in range(rounds + 1):
        for shared in ((False, True) if index % 2 == 0 else (True, False)):
            result = _run_case(
                shared, jobs_per_pool, io_ms, coordinator_policy=coordinator_policy,
            )
            if index:  # exclude one warmup pair
                samples.append({"round": index, **result})
    return {
        "schema_version": 2, "measurement_origin": "simulated",
        "environment": {"python": platform.python_version(), "platform": sys.platform},
        "workload": {"workers": [3, 3, 8], "jobs_per_pool": jobs_per_pool,
                     "synthetic_io_ms": io_ms, "user_fraction": "every_fourth_job",
                     "max_inflight": 12, "reserve_user_slots": 2, "max_per_scope": 4},
        "method": {"warmup_pairs_excluded": 1, "order": "alternating",
                   "percentiles": "nearest_rank", "probe_overhead_included": True,
                   "end_to_end_includes_coordinator_retry_wait": True,
                   "shared_coordinator_policy": coordinator_policy,
                   "fair_policy_burst": 3 if coordinator_policy == "fair_round_robin" else None},
        "samples": samples,
        "limits": {"real_integrations_exercised": False, "gtk_latency_measured": False,
                   "network_used": False, "private_data_used": False,
                   "sampled_pending_is_not_exact_high_water": True,
                   "rss_is_current_process_before_after_not_peak": True,
                   "same_process_rss_includes_prior_rounds": True,
                   "fair_policy_not_production_integrated": True,
                   "continuous_arrival_fairness_not_proven": True},
        "performance_verdict": "not_qualified",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--jobs-per-pool", type=int, default=64)
    parser.add_argument("--io-ms", type=int, default=2)
    parser.add_argument(
        "--coordinator-policy",
        choices=("legacy_scan", "fair_round_robin"),
        default="legacy_scan",
    )
    args = parser.parse_args(argv)
    try:
        report = benchmark(
            rounds=args.rounds, jobs_per_pool=args.jobs_per_pool, io_ms=args.io_ms,
            coordinator_policy=args.coordinator_policy,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
