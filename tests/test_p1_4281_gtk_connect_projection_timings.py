"""#4281 — Synthetic GTK callback timings after a provider connection.

These timings distinguish MainWindow projection time from the TaskRunner
provider worker. Never use them as evidence of Paris HTTP latency.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui import query_widget_factory_controller, startup_coordinator
from cifereca.gui.ui_perf import UiPerfMonitor


def test_connection_callback_reports_two_closed_gtk_phases_without_provider_io(monkeypatch):
    calls = []
    clock = iter((10.0, 10.125, 20.0, 20.045))
    monkeypatch.setattr(startup_coordinator.time, "perf_counter", lambda: next(clock))
    monkeypatch.setattr(
        query_widget_factory_controller, "refresh_query_providers",
        lambda _window: calls.append("query-options"),
    )
    runner = NS(submit=Mock())
    service = NS(last_loans=(object(),), connect_configured=Mock())
    monitor = NS(record_phase=lambda *args, **kwargs: calls.append((args, kwargs)))
    host = NS(
        _closed=False, service=service, runner=runner, ui_perf=monitor,
        _sync_sources_box=None,
        _populate_accounts=lambda: calls.append("accounts"),
        _populate_loans=lambda rows: calls.append(("loans", len(rows))),
        _activity_result=lambda *_args, **_kwargs: calls.append("feedback"),
        _render_dashboard=lambda: calls.append("dashboard"),
        run_opening_sync=lambda: calls.append("opening-sync"),
    )
    idles = []
    assert startup_coordinator.connect_configured_sources(
        host, idle_add=idles.append,
    ) is False
    # The network worker has been *submitted*, not executed by this fixture.
    service.connect_configured.assert_not_called()
    assert runner.submit.call_count == 1
    worker_args = runner.submit.call_args.args
    worker_args[2]({"connected": [], "errors": {}})  # GTK delivery callback
    service.connect_configured.assert_not_called()
    phases = [entry for entry in calls if isinstance(entry, tuple)
              and len(entry) == 2 and isinstance(entry[0], tuple)]
    assert len(phases) == 2
    assert phases[0][0][0:2] == ("provider-connect", "model_update")
    assert phases[0][0][2] == pytest.approx(125)
    assert phases[1][0][0:2] == ("provider-connect", "render")
    assert phases[1][0][2] == pytest.approx(45)
    assert all(kwargs == {"trigger": "unspecified", "outcome": "success"}
               for _, kwargs in phases)
    assert calls.index("accounts") < calls.index("query-options")
    assert ("loans", 1) in calls
    assert len(idles) == 1
    assert not any("PRIVATE" in repr(event) for event in phases)


@pytest.mark.parametrize("phase", ["model_update", "render"])
def test_recording_is_safe_when_absent_broken_or_window_closed(phase):
    def broken(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_USER_OR_URL")

    for monitor in (None, NS(record_phase=broken), NS(record_phase=object())):
        host = NS(_closed=False, ui_perf=monitor)
        startup_coordinator._record_post_connect_ui_phase(host, phase, 0.0)
    on_close = Mock()
    startup_coordinator._record_post_connect_ui_phase(
        NS(_closed=True, ui_perf=NS(record_phase=on_close)), phase, 0.0,
    )
    on_close.assert_not_called()


def test_phase_names_are_accepted_by_real_bounded_perf_contract():
    monitor = UiPerfMonitor(measurement_origin="simulated")
    assert monitor.record_phase(
        "provider-connect", "model_update", 125.0, trigger="unspecified",
        outcome="success",
    )
    assert monitor.record_phase(
        "provider-connect", "render", 45.0, trigger="unspecified",
        outcome="success",
    )
    assert not monitor.record_phase("PRIVATE_ACCOUNT", "render", float("nan"))
