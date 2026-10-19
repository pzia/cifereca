import json
from types import SimpleNamespace

import pytest

from diagnostics_performance import PerformanceCapture, performance_bytes


def report(enabled=False):
    return {"schema": "cifereca.performance.phases/1",
            "detailed_capture_enabled": enabled, "remote_upload": False,
            "stats": {"render": {"count": 7, "p95_ms": 12.5}}, "events": []}


def test_passive_read_detaches_snapshot_and_navigation_preserves_capture():
    state = report()
    mutations = []
    def set_enabled(value):
        mutations.append(value)
        state["detailed_capture_enabled"] = value
        return state
    host = SimpleNamespace(performance_report=lambda: state,
                           set_performance_capture=set_enabled)
    first = PerformanceCapture(host)
    snapshot = first.read()
    snapshot["stats"].clear()
    assert first.read()["stats"]["render"]["count"] == 7
    assert mutations == []
    assert first.set_enabled(True)["detailed_capture_enabled"] is True
    second = PerformanceCapture(host)
    assert second.read()["detailed_capture_enabled"] is True
    assert mutations == [True]
    assert second.set_enabled(False)["detailed_capture_enabled"] is False
    assert mutations == [True, False]


@pytest.mark.parametrize("value", [1, 0, None, "false", [], object()])
def test_capture_requires_exact_boolean_without_mutation(value):
    calls = []
    host = SimpleNamespace(set_performance_capture=lambda v: calls.append(v))
    with pytest.raises(ValueError):
        PerformanceCapture(host).set_enabled(value)
    assert calls == []


def test_old_core_is_optional_and_export_preserves_complete_measurements():
    with pytest.raises(ValueError):
        PerformanceCapture(object()).read()
    value = report()
    value["events"] = [{"phase": "render", "duration_ms": 5.25}] * 240
    assert json.loads(performance_bytes(value)) == value
    assert len(json.loads(performance_bytes(value))["events"]) == 240


@pytest.mark.parametrize("value", [None, {}, {"schema": "unknown"},
    {**report(), "remote_upload": True}, {**report(), "detailed_capture_enabled": 1},
    {**report(), "stats": float("nan")}, {**report(), "events": ["x" * 1_000_001]}])
def test_invalid_nonfinite_or_oversized_snapshot_refused_without_partial_json(value):
    with pytest.raises((ValueError, TypeError)):
        performance_bytes(value)
