"""P1 #4277 — first-launch provider connection is automatic, not a user click.

No GTK, login, network or plugin activation is performed. This protects one
specific unsolicited-modal path without claiming it caused the field incident.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.startup_coordinator import connect_configured_sources
from tests.test_core_p0_981_redacted_task_traces import _runner


@pytest.mark.parametrize(("already_connected", "hint", "expected_origin"), [
    (False, None, "automatic"),
    (True, "plugin_activation", "automatic"),
    (True, None, "user"),
    (True, "unknown", "user"),
])
def test_provider_connection_origin_follows_attested_startup_or_user_reconnect(
    already_connected, hint, expected_origin,
):
    runner = NS(submit=Mock())
    service = NS(connect_configured=Mock())
    host = NS(
        _closed=False, service=service, runner=runner,
        _source_inventory_startup_connection_seen=already_connected,
        _provider_connect_caller_hint=hint,
    )
    assert connect_configured_sources(host, idle_add=lambda callback: None) is False
    assert runner.submit.call_count == 1
    assert runner.submit.call_args.kwargs["kind"] == "provider-connect"
    assert runner.submit.call_args.kwargs["origin"] == expected_origin
    service.connect_configured.assert_not_called()


@pytest.mark.parametrize(("origin", "expected_modals"), [
    ("automatic", 0), ("user", 1),
])
def test_failed_gtk_completion_of_provider_connect_stays_in_activity_without_automatic_modal(
    origin, expected_modals,
):
    runner, displayed, flush, logger = _runner()
    activity = Mock()
    runner.window._activity_result = activity
    def fail_after_worker_succeeded(_result):
        raise RuntimeError("synthetic first-launch GTK projection error")
    try:
        runner.submit(
            "Connexion aux providers…", lambda: {"connected": [], "errors": {}},
            ok=fail_after_worker_succeeded, kind="provider-connect",
            origin=origin,
        )
        flush()
        assert len([event for event, _ in displayed if event == "finished"]) == 1
        assert activity.call_count == 1
        assert activity.call_args.kwargs["success"] is False
        assert runner.window.show_error.call_count == expected_modals
    finally:
        runner.close()
