"""Explicit offline microbenchmark of existing Core diagnostics producers (#1741).

No application, settings, providers or GTK are started. Sampled durations are
the cost of calling the producers with fixed synthetic data, not UI latency.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import time

from cifereca.diagnostics import Diagnostics
from cifereca.gui.ui_perf import UiPerfMonitor


CASES = (
    "baseline", "diagnostics_disabled", "diagnostics_errors",
    "diagnostics_normal", "diagnostics_detailed", "monitor_passive", "monitor_detailed",
)


def _case(name):
    if name == "baseline":
        return lambda: None, lambda: {}, lambda: None
    if name.startswith("diagnostics_"):
        level = name.removeprefix("diagnostics_")
        collector = Diagnostics(enabled=level != "disabled", level=level, max_entries=500)
        detail = {"count": 1, "url": "https://synthetic.invalid/?token=SYNTHETIC-TOKEN"}
        def action():
            collector.add("BENCHMARK", "synthetic", detail=detail)
            collector.add("BENCHMARK", "synthetic detail", detail=detail, level="detailed")
        return action, lambda: {"diagnostic_entries": len(collector.snapshot())}, lambda: None
    monitor = UiPerfMonitor(measurement_origin="simulated")
    detailed = name == "monitor_detailed"
    if detailed:
        monitor.enable_detailed_capture(True)
    def action():
        # Fixed fixture values; the benchmark times the calls, not these
        # synthetic business durations. No slow-operation log is emitted.
        monitor.record("render_library", 1.25)
        monitor.record_phase("library", "worker", 1.25, operation_id=1)
    def retained():
        report = monitor.performance_report()
        return {
            "passive_samples": monitor.snapshot()["render_library"]["count"],
            "phase_samples": report["stats"]["library.worker"]["count"],
            "timeline_events": len(report["events"]),
            "timeline_events_dropped": report["events_dropped"],
        }
    return action, retained, lambda: monitor.enable_detailed_capture(False) if detailed else None


def _distribution(values):
    ordered = sorted(values)
    return {
        "count": len(values),
        "p50_us": round(statistics.median(ordered), 3),
        "p95_us": round(ordered[math.ceil(len(ordered) * .95) - 1], 3),
        "stddev_us": round(statistics.pstdev(ordered), 3),
        "variance_us2": round(statistics.pvariance(ordered), 6),
        "min_us": round(ordered[0], 3),
        "max_us": round(ordered[-1], 3),
    }


def benchmark(*, blocks=30, iterations=100, wall_clock=None, cpu_clock=None):
    """Bounded, balanced same-process samples; setup/export outside timers."""
    if (type(blocks) is not int or not 5 <= blocks <= 100
            or type(iterations) is not int or not 10 <= iterations <= 10000
            or blocks * iterations > 100000):
        raise ValueError("benchmark_sample_budget_invalid")
    wall_clock = wall_clock or time.perf_counter_ns
    cpu_clock = cpu_clock or time.process_time_ns
    latency = {name: [] for name in CASES}
    cpu = {name: [] for name in CASES}
    retained = {}
    # First full block is warmup and excluded. Rotating the start position
    # prevents any one mode from always running before/after all others.
    for block in range(blocks + 1):
        offset = block % len(CASES)
        for name in CASES[offset:] + CASES[:offset]:
            action, counters, close = _case(name)
            observed = []
            try:
                cpu_start = cpu_clock()
                for _ in range(iterations):
                    start = wall_clock()
                    action()
                    elapsed = (wall_clock() - start) / 1000
                    observed.append(elapsed)
                process_us = (cpu_clock() - cpu_start) / 1000 / iterations
                if block:
                    latency[name].extend(observed)
                    cpu[name].append(process_us)
                    retained[name] = counters()
            finally:
                close()
    return {
        "schema": "cifereca.diagnostics.microbenchmark/1",
        "measurement_origin": "synthetic_headless",
        "python": platform.python_version(),
        "protocol": {
            "blocks": blocks, "iterations_per_block": iterations,
            "warmup_blocks_excluded": 1, "order": "rotating",
            "percentile_method": "nearest_rank", "probe_overhead_included": True,
            "setup_export_and_capture_transitions_timed": False,
            "baseline_subtracted": False,
        },
        "cases": {
            name: {
                "call_latency": _distribution(latency[name]),
                "process_cpu_per_action": _distribution(cpu[name]),
                "retained_last_block": retained[name],
            } for name in CASES
        },
        "limits": {
            "rss_measured": False, "gtk_frames_measured": False,
            "file_logging_measured": False, "plugin_lifecycle_measured": False,
            "network_used": False, "private_data_used": False,
        },
        "performance_verdict": "not_qualified",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blocks", type=int, default=30)
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        report = benchmark(blocks=args.blocks, iterations=args.iterations)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
