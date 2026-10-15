"""#1741: passive UI monitor is bounded and safe even with malicious page names."""
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest

from cifereca.gui.ui_perf import UiPerfMonitor


@pytest.mark.parametrize("operation", [
    "render_library", "render_recent", "render",
    "startup.window_presented", "startup.plugins_activation",
    "startup.shell_interactive", "list.append-fast", "list.replace",
    "list.append",
])
def test_public_core_legacy_names_keep_exact_snapshot_keys(operation):
    monitor = UiPerfMonitor()
    monitor.record(operation, 12.5)
    assert list(monitor.snapshot()) == [operation]
    assert monitor.snapshot()[operation]["count"] == 1


def test_untrusted_titles_cannot_be_retained_in_snapshot_or_slow_trace(caplog):
    monitor = UiPerfMonitor(max_samples=20, slow_ms=0)
    secret = "Compte https://private.invalid/?token=BEARER_SECRET"
    with caplog.at_level(logging.INFO, logger="cifereca"):
        monitor.record("list:" + secret + ":replace", 85.0)
        monitor.record(secret, 120.0)
        with monitor.measure(secret):
            pass
    snap = monitor.snapshot()
    assert set(snap) == {"other"}
    assert snap["other"]["count"] == 3
    assert secret not in caplog.text
    assert "BEARER_SECRET" not in caplog.text
    assert "operation=other" in caplog.text
    assert "BEARER_SECRET" not in json.dumps(snap)


def test_legacy_stats_cardinality_bounded_under_large_untrusted_plugin_names():
    monitor = UiPerfMonitor(max_samples=20, slow_ms=100000)
    for index in range(600):
        monitor.record(f"private-{index}/?credential=SECRET", index)
    snap = monitor.snapshot()
    assert set(snap) == {"other"}
    assert snap["other"]["count"] == 20
    assert snap["other"]["max_ms"] == 599
    assert "SECRET" not in json.dumps(snap)


@pytest.mark.parametrize("value", [
    -1, float("nan"), float("inf"), float("-inf"), True,
    "Authorization: Bearer SECRET", None, 86_400_001,
])
def test_invalid_legacy_durations_are_dropped_without_crashing_or_logging(value, caplog):
    monitor = UiPerfMonitor()
    with caplog.at_level(logging.INFO, logger="cifereca"):
        monitor.record("render_library", value)
    assert monitor.snapshot() == {}
    assert monitor.performance_report()["invalid_samples"] == 1
    assert "SECRET" not in caplog.text


def test_master_detail_probes_have_fixed_operation_names_not_ui_title():
    """The source of operations is safe, not merely the export sanitizer."""
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/cifereca/gui/master_detail.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "MasterDetailPage")
    calls = [
        n for n in ast.walk(cls)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "record"
        and isinstance(n.func.value, ast.Attribute)
        and n.func.value.attr == "ui_perf"
    ]
    assert len(calls) == 3
    assert {n.args[0].value for n in calls
            if isinstance(n.args[0], ast.Constant)} == {
                "list.append", "list.append-fast", "list.replace",
            }
    assert all(isinstance(n.args[0], ast.Constant) for n in calls)
