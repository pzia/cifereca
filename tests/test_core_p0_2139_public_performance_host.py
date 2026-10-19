"""Public optional UI performance host: explicit capture, shared Core lifetime."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.plugin_api import UiPageHost, UiPerformanceHost, UnsupportedOperation
from cifereca.gui.ui_perf import UiPerfMonitor
from tests.test_plugin_ui_host_boundary import host_type


def observer():
    return UiPerfMonitor(resource_reader=lambda: {"cpu_ms": 0.0, "rss_bytes": None})


def test_optional_protocol_keeps_minimal_old_host_compatible():
    old = NS(toast=Mock(), navigate=Mock(), plugin_statuses=Mock())
    assert isinstance(old, UiPageHost)
    assert not isinstance(old, UiPerformanceHost)
    host = host_type()(NS(ui_perf=observer()))
    assert isinstance(host, UiPerformanceHost)


def test_read_is_passive_and_shared_capture_survives_new_page_host():
    monitor = observer()
    token = object()
    window = NS(ui_perf=monitor, _closed=False, _shell_page_rebuild_token=token)
    old = host_type()(window)
    assert old.performance_report()["detailed_capture_enabled"] is False
    assert monitor.detailed_capture_enabled is False
    assert old.set_performance_capture(True)["detailed_capture_enabled"] is True
    generation = monitor.capture_generation
    # Navigating/rebuilding a page neither stops nor restarts the monitor.
    window._shell_page_rebuild_token = object()
    new = host_type()(window)
    assert new.performance_report()["detailed_capture_enabled"] is True
    assert monitor.capture_generation == generation
    for action in (old.performance_report, lambda: old.set_performance_capture(False)):
        with pytest.raises(UnsupportedOperation):
            action()
    assert monitor.detailed_capture_enabled is True
    assert new.set_performance_capture(False)["detailed_capture_enabled"] is False


@pytest.mark.parametrize("bad", [None, 0, 1, "true", [], {}, 1.0])
def test_capture_never_coerces_arbitrary_values_to_consent(bad):
    monitor = observer()
    host = host_type()(NS(ui_perf=monitor))
    with pytest.raises(ValueError, match="invalid_performance_capture_option"):
        host.set_performance_capture(bad)
    assert monitor.detailed_capture_enabled is False


def test_closed_window_refuses_read_and_mutation_without_touching_monitor():
    monitor = Mock()
    host = host_type()(NS(ui_perf=monitor, _closed=True))
    with pytest.raises(UnsupportedOperation):
        host.performance_report()
    with pytest.raises(UnsupportedOperation):
        host.set_performance_capture(True)
    monitor.performance_report.assert_not_called()
    monitor.enable_detailed_capture.assert_not_called()


def test_missing_monitor_is_a_fixed_optional_capability_error():
    host = host_type()(NS())
    with pytest.raises(UnsupportedOperation, match="Observation des performances indisponible"):
        host.performance_report()


@pytest.mark.parametrize("method", ["performance_report", "enable_detailed_capture"])
def test_internal_errors_are_fixed_and_unchained(method):
    class Hostile(RuntimeError):
        def __str__(self):
            raise AssertionError("private exception stringified")
    monitor = Mock()
    monitor.performance_report.return_value = {"schema":"cifereca.performance.phases/1"}
    getattr(monitor, method).side_effect = Hostile("PRIVATE_SESSION")
    host = host_type()(NS(ui_perf=monitor))
    with pytest.raises(UnsupportedOperation) as failure:
        if method == "performance_report":
            host.performance_report()
        else:
            host.set_performance_capture(True)
    assert "PRIVATE_SESSION" not in str(failure.value)
    assert failure.value.__suppress_context__


def test_report_is_existing_versioned_safe_json_and_mutation_does_not_change_monitor():
    monitor = observer()
    host = host_type()(NS(ui_perf=monitor))
    host.set_performance_capture(True)
    report = host.performance_report()
    assert json.loads(json.dumps(report, allow_nan=False))["schema"] == "cifereca.performance.phases/1"
    assert report["remote_upload"] is False
    report["stats"]["PRIVATE_SESSION"] = {"path":"/home/private"}
    assert "PRIVATE_SESSION" not in repr(host.performance_report())
