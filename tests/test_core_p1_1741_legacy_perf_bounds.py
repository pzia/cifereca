"""P1 #1741: legacy UI snapshots are bounded and never retain private labels."""
from __future__ import annotations

import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor

import pytest

from cifereca.gui.ui_perf import UiPerfMonitor


def test_existing_named_startup_and_render_metrics_remain_available():
    monitor = UiPerfMonitor(slow_ms=1000)
    expected = (
        "render_library", "render_recent", "startup.window_presented",
        "startup.plugins_activation", "startup.shell_interactive",
    )
    for index, name in enumerate(expected, 1):
        monitor.record(name, float(index))
    snapshot = monitor.snapshot()
    assert set(snapshot) == set(expected)
    for index, name in enumerate(expected, 1):
        assert snapshot[name] == {
            "count": 1, "p50_ms": float(index),
            "p95_ms": float(index), "max_ms": float(index),
        }
    assert monitor.performance_report()["no_samples"] is True


@pytest.mark.parametrize("operation,expected", [
    ("list:Bibliothèque:append", "list.append"),
    ("list:Recherche:replace", "list.replace"),
    ("list:private:append-fast", "list.append-fast"),
    ("list:PRIVATE_TOKEN:append", "list.append"),
    ("list:PRIVATE_TOKEN:unknown", "other"),
    ("https://private.invalid/?token=PRIVATE_TOKEN", "other"),
    ({"secret": "PRIVATE_TOKEN"}, "other"),
    (None, "other"),
    (True, "other"),
    ("list::replace", "other"),
])
def test_private_or_unknown_legacy_operation_never_survives_projection(operation, expected):
    monitor = UiPerfMonitor(slow_ms=0)
    monitor.record(operation, 12)
    report = json.dumps(monitor.snapshot())
    assert set(monitor.snapshot()) == {expected}
    for secret in ("PRIVATE_TOKEN", "private.invalid", "Bibliothèque", "Recherche"):
        assert secret not in report


def test_slow_trace_is_redacted_but_includes_elapsed_and_coarse_operation(caplog):
    monitor = UiPerfMonitor(slow_ms=2)
    secret = "https://private.invalid/?bearer=SECRET_PASSWORD"
    with caplog.at_level(logging.INFO, logger="cifereca"):
        monitor.record(f"list:{secret}:replace", 12.25)
        monitor.record(secret, 7)
        with monitor.measure(f"list:{secret}:append"):
            pass
    assert secret not in caplog.text
    assert "SECRET_PASSWORD" not in json.dumps(monitor.snapshot())
    assert "operation=list.replace" in caplog.text
    assert "duration_ms=12.2" in caplog.text
    assert "operation=other" in caplog.text
    assert set(monitor.snapshot()) == {"list.replace", "list.append", "other"}


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, 86_400_001,
                                      "PRIVATE_TOKEN", None, True, [], {}])
def test_invalid_legacy_durations_never_poison_stats_or_logs(invalid, caplog):
    monitor = UiPerfMonitor(slow_ms=0)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        monitor.record("render_library", invalid)
    assert monitor.snapshot() == {}
    assert "slow operation" not in caplog.text


def test_free_form_labels_do_not_increase_series_count_or_unbounded_storage():
    monitor = UiPerfMonitor(max_samples=20, slow_ms=math.inf)
    for number in range(5000):
        monitor.record(f"list:account_{number}:replace", float(number % 9))
        monitor.record(f"https://private.invalid/{number}", float(number % 9))
    snap = monitor.snapshot()
    assert set(snap) == {"list.replace", "other"}
    assert snap["list.replace"]["count"] == 20
    assert snap["other"]["count"] == 20
    assert "account_" not in json.dumps(snap)


def test_threaded_producers_share_only_bounded_public_series():
    monitor = UiPerfMonitor(max_samples=30, slow_ms=math.inf)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(
            lambda index: monitor.record(f"list:private_{index}:append", 4),
            range(1000),
        ))
    assert monitor.snapshot()["list.append"]["count"] == 30
    assert set(monitor.snapshot()) == {"list.append"}
