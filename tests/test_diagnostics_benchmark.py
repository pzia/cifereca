"""Contract of the explicit microbenchmark, never a speed-based CI gate."""
import itertools
import json

import pytest

from scripts import benchmark_diagnostics as bench


def run():
    wall = itertools.count(step=1000)
    cpu = itertools.count(step=10000)
    return bench.benchmark(blocks=5, iterations=10,
                           wall_clock=lambda: next(wall), cpu_clock=lambda: next(cpu))


def test_warmup_is_excluded_and_report_has_measured_counts_and_variance():
    report = run()
    assert tuple(report["cases"]) == bench.CASES
    for case in report["cases"].values():
        assert case["call_latency"] == {
            "count": 50, "p50_us": 1.0, "p95_us": 1.0, "stddev_us": 0.0,
            "variance_us2": 0.0,
            "min_us": 1.0, "max_us": 1.0,
        }
        assert case["process_cpu_per_action"]["count"] == 5
        assert case["process_cpu_per_action"]["p50_us"] == 1
    assert report["protocol"]["baseline_subtracted"] is False
    assert report["performance_verdict"] == "not_qualified"
    assert "SYNTHETIC-TOKEN" not in json.dumps(report, allow_nan=False)


def test_small_sample_p95_uses_nearest_rank_and_population_variance():
    result = bench._distribution(list(range(1, 15)))
    assert result["p95_us"] == 14
    assert result["p50_us"] == 7.5
    assert result["variance_us2"] == 16.25


def test_actual_producers_have_distinct_retention_without_a_second_collector():
    cases = run()["cases"]
    assert cases["diagnostics_disabled"]["retained_last_block"]["diagnostic_entries"] == 0
    assert cases["diagnostics_errors"]["retained_last_block"]["diagnostic_entries"] == 0
    assert cases["diagnostics_normal"]["retained_last_block"]["diagnostic_entries"] == 10
    assert cases["diagnostics_detailed"]["retained_last_block"]["diagnostic_entries"] == 20
    assert cases["monitor_passive"]["retained_last_block"]["timeline_events"] == 0
    assert cases["monitor_detailed"]["retained_last_block"]["timeline_events"] == 10


def test_long_blocks_preserve_the_real_producers_retention_limits():
    for name in ("diagnostics_normal", "diagnostics_detailed", "monitor_detailed"):
        action, counters, close = bench._case(name)
        try:
            for _ in range(501):
                action()
            retained = counters()
            if name.startswith("diagnostics_"):
                assert retained["diagnostic_entries"] == 500
            else:
                assert retained == {"passive_samples": 200, "phase_samples": 200,
                                    "timeline_events": 240, "timeline_events_dropped": 261}
        finally:
            close()


def test_order_rotates_and_all_modes_run_once_per_block(monkeypatch):
    order = []
    original = bench._case
    def case(name):
        order.append(name)
        return original(name)
    monkeypatch.setattr(bench, "_case", case)
    run()
    for block in range(6):
        offset = block % len(bench.CASES)
        assert order[block * 7:(block + 1) * 7] == list(bench.CASES[offset:] + bench.CASES[:offset])


@pytest.mark.parametrize("blocks,iterations", [(True, 10), (4, 10), (101, 10),
    (5, True), (5, 9), (5, 10001), (100, 10000), ("5", 10)])
def test_invalid_budgets_are_rejected_before_any_work(blocks, iterations, monkeypatch):
    monkeypatch.setattr(bench, "_case", lambda name: pytest.fail("budget rejected before setup"))
    with pytest.raises(ValueError, match="benchmark_sample_budget_invalid"):
        bench.benchmark(blocks=blocks, iterations=iterations)


def test_console_output_is_json_and_invalid_cli_budget_is_rejected(capsys):
    bench.main(["--blocks", "5", "--iterations", "10"])
    assert json.loads(capsys.readouterr().out)["measurement_origin"] == "synthetic_headless"
    with pytest.raises(SystemExit) as error:
        bench.main(["--blocks", "0"])
    assert error.value.code == 2
