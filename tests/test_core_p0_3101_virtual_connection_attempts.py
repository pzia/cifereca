"""#3101: plugin-activated virtual sessions are real, correlatable requests.

Only synthetic in-memory providers: no BnF-Prêt, HTTP, ADEPT or private exports.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core import provider_sessions
from cifereca.core.diagnostic_event_feed import _source_diagnostics
from cifereca.diagnostics import Diagnostics


PRIVATE = "PRIVATE_CREDENTIAL_TOKEN_XYZ"
PID = "virtual.test"


def _rig(*, connected_on_return=True, throws=False):
    diagnostics = Diagnostics()
    observed = {"connect": 0, "close": 0}
    session = NS(
        provider_id=PID, connected=False,
    )
    def connect():
        observed["connect"] += 1
        if throws:
            raise RuntimeError(PRIVATE + " https://private.invalid/path?token=SECRET")
        session.connected = connected_on_return
    def close():
        observed["close"] += 1
        session.connected = False
    session.connect = connect
    session.close = close
    plugin = NS(
        descriptor=NS(id=PID, display_name="PRIVATE_ACCOUNT_LABEL"),
        create_session=Mock(return_value=session),
    )
    registry = NS(maybe_get=lambda key: plugin if key == PID else None)
    service = NS(
        registry=registry,
        _sessions={}, _session_errors={},
        _session_key=lambda provider, credential: provider + ":" + credential,
        config=NS(), storage=NS(), diagnostics=diagnostics,
    )
    return service, session, plugin, observed


def _attempts(service):
    rows, _ = _source_diagnostics(service.diagnostics)
    return [
        e["detail"] for e in rows
        if e.get("detail", {}).get("source_kind") == "virtual"
    ]


def test_virtual_plugin_activation_has_one_request_one_real_call_and_terminal():
    service, session, plugin, observed = _rig()
    provider_sessions._connect_changed_virtual_provider(service, PID)
    assert service._sessions["virtual.test:__public__"] is session
    assert observed["connect"] == 1
    assert plugin.create_session.call_count == 1
    events = _attempts(service)
    assert [x["phase"] for x in events] == ["requested", "connect_invoked", "terminal"]
    assert [x["status"] for x in events] == ["unknown", "unknown", "success"]
    assert len({x["operation_ref"] for x in events}) == 1
    assert all(x["caller_category"] == "plugin_activation" for x in events)
    assert all(x["network_auth"] == "unknown" for x in events)
    assert all("scope_ref" not in e for e in events)  # public singleton, no identity
    assert PRIVATE not in json.dumps(events)


def test_virtual_second_demand_is_reuse_not_second_network_or_provider_call():
    service, session, plugin, observed = _rig()
    provider_sessions._connect_changed_virtual_provider(service, PID)
    provider_sessions._connect_changed_virtual_provider(service, PID)
    assert observed["connect"] == 1
    assert plugin.create_session.call_count == 1
    events = _attempts(service)
    assert [e["phase"] for e in events] == [
        "requested", "connect_invoked", "terminal",
        "requested", "session_reused", "terminal",
    ]
    first, second = events[0]["operation_ref"], events[3]["operation_ref"]
    assert first != second
    assert len({e["operation_ref"] for e in events}) == 2


@pytest.mark.parametrize("throws", [False, True])
def test_false_or_throwing_virtual_connect_does_not_publish_session_or_success(throws):
    service, session, plugin, observed = _rig(connected_on_return=False, throws=throws)
    provider_sessions._connect_changed_virtual_provider(service, PID)
    assert not service._sessions
    assert service._session_errors == {
        "virtual.test:__public__": "virtual_provider_connect_failed",
    }
    assert observed["close"] == 1
    events = _attempts(service)
    assert [e["phase"] for e in events] == [
        "requested", "connect_invoked", "terminal",
    ]
    assert events[-1]["status"] == "failed"
    assert events[-1]["reason_code"] == "connect_failed"
    assert PRIVATE not in json.dumps(events) + service.diagnostics.text("support")


def test_stale_virtual_session_is_replaced_not_implicitly_reused():
    service, session, plugin, observed = _rig()
    stale_closed = []
    service._sessions["virtual.test:__public__"] = NS(
        provider_id=PID, connected=False, close=lambda: stale_closed.append(True)
    )
    provider_sessions._connect_changed_virtual_provider(service, PID)
    assert stale_closed == [True]
    assert service._sessions["virtual.test:__public__"] is session
    assert observed["connect"] == 1
    events = _attempts(service)
    assert [e["phase"] for e in events] == [
        "requested", "connect_invoked", "terminal",
    ]


def test_invalid_reused_virtual_provider_cannot_be_trusted():
    service, session, plugin, observed = _rig()
    service._sessions["virtual.test:__public__"] = NS(
        provider_id="foreign", connected=True, close=lambda: None
    )
    provider_sessions._connect_changed_virtual_provider(service, PID)
    assert service._sessions["virtual.test:__public__"] is session
    assert observed["connect"] == 1
    assert not any(e["phase"] == "session_reused" for e in _attempts(service))


def test_missing_virtual_plugin_is_one_skipped_request_not_false_success():
    service, session, plugin, observed = _rig()
    service.registry.maybe_get = lambda _key: None
    provider_sessions._connect_changed_virtual_provider(service, PID)
    assert observed["connect"] == 0
    assert not service._sessions
    events = _attempts(service)
    assert [e["phase"] for e in events] == ["requested", "terminal"]
    assert events[-1]["status"] == "skipped"
    assert events[-1]["reason_code"] == "plugin_missing"
    assert events[-1]["provider_id"] == "source_inconnue"


def test_constructor_failure_terminal_is_not_counted_as_connect_invoked():
    service, session, plugin, observed = _rig()
    plugin.create_session.side_effect = RuntimeError(PRIVATE)
    provider_sessions._connect_changed_virtual_provider(service, PID)
    assert not service._sessions
    assert observed["connect"] == 0
    events = _attempts(service)
    assert [e["phase"] for e in events] == ["requested", "terminal"]
    assert events[-1]["reason_code"] == "session_invalid"
    assert PRIVATE not in json.dumps(events)
