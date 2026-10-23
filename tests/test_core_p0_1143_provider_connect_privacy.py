"""P0 #1143: provider connect diagnostics and logs never expose connector secrets.

The real Core adapters are called with synchronous fake sessions and storage:
no GTK, provider network, disk writes or external plugin activation.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core import provider_sessions
from cifereca.diagnostics import Diagnostics


SECRET = "PRIVATE_ACCOUNT_CREDENTIAL"
SIGNED = "https://private.invalid/LOAN/book.acsm?auth=SECRET_TOKEN"


class HostileFailure(RuntimeError):
    def __str__(self):
        raise AssertionError("Never stringify an untrusted provider exception")

    def __repr__(self):
        raise AssertionError("Never repr an untrusted provider exception")


def _rig(*, singleton=False, succeeds=False):
    diag = Diagnostics(level="detailed")
    cred = "__public__" if singleton else SECRET
    account = NS(provider_id="provider.test", credential_id=cred,
                 unique_id=SECRET, identity_id="identity-private",
                 display_name="Private Label", is_primary=True, barcode=SECRET)
    session = NS(
        close=Mock(), connected=False,
        connect=Mock(return_value=[account] if succeeds else None),
    )
    if not succeeds:
        session.connect.side_effect = HostileFailure(SIGNED)
    else:
        def completed_connect():
            session.connected = True
            return [account]
        session.connect.side_effect = completed_connect
    descriptor = NS(
        id="provider.test", state="ready",
        credential_fields=() if singleton else ("password",),
        display_name="Private label",
    )
    plugin = NS(descriptor=descriptor, create_session=Mock(return_value=session))
    rows = [] if singleton else [{
        "id": cred, "provider": "provider.test", "identity_id": "identity-private",
        "credentials": {"password": SECRET}, "enabled": True,
    }]
    state = NS(
        _sessions={}, _session_errors={}, _virtual_parent_by_provider={},
        current_identity=NS(identity_id="identity-private"), identities=[],
        config=NS(provider_accounts=lambda **kw: list(rows)),
        registry=NS(all=lambda: [plugin], maybe_get=lambda _id: plugin,
                    diagnostic_state=lambda: {"providers": 1}),
        diagnostics=diag,
        storage=NS(provider_connection_put=lambda _row: 1,
                   provider_account_put=Mock()),
        _provider_plugin_status=lambda _id: NS(descriptor=NS(
            instantiation="singleton_public" if singleton else "multiple")),
        _session_key=lambda pid, cid: f"{pid}:{cid}",
        _local_account=lambda: NS(identity_id="local", credential_id="local"),
        identity_by_id=lambda identity: NS(identity_id=identity),
        refresh_identity_loans=lambda **kwargs: [],
        kobo_adept_state=lambda: {"available": True},
    )
    state.disconnect_configured = lambda: provider_sessions.disconnect_configured(state)
    return state, session, plugin


@pytest.mark.parametrize("singleton", [False, True])
def test_failed_provider_connect_has_closed_error_and_no_traceback(
    caplog, singleton,
):
    state, session, plugin = _rig(singleton=singleton)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        result = provider_sessions.connect_configured(
            state, logger=logging.getLogger("cifereca"))
    session.close.assert_called_once()
    key = "provider.test:" + ("__public__" if singleton else SECRET)
    assert result["errors"] == {key: "Connexion à la source indisponible"}
    assert state._session_errors == result["errors"]
    assert result["connected"] == []
    details = [r["detail"] for r in state.diagnostics.snapshot()
               if r["operation"] in {"provider connect", "public provider connect"}]
    assert details == [{
        "code": "provider_connect_failed", "provider_id": "provider.test",
    }]
    snapshot = provider_sessions.provider_diagnostic_state(state)
    assert snapshot["errors"] == {"connect_failed": 1}
    visible = caplog.text + state.diagnostics.text("test") + json.dumps(snapshot)
    for marker in (SECRET, "SECRET_TOKEN", "private.invalid", "HostileFailure",
                   "identity-private", "private label", "private path"):
        assert marker not in visible
    assert "TRACE provider connect error code=provider_connect_failed" in caplog.text if not singleton else True


def test_successful_configured_provider_keeps_account_behavior_without_credential_log(caplog):
    state, session, plugin = _rig(succeeds=True)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        result = provider_sessions.connect_configured(
            state, logger=logging.getLogger("cifereca"))
    assert len(result["accounts"]) == 1
    assert result["accounts"][0].unique_id == SECRET
    assert result["connected"] == ["provider.test:" + SECRET]
    assert state.storage.provider_account_put.call_count == 1
    assert "TRACE provider connect end account_count=1" in caplog.text
    assert "PRIVATE_ACCOUNT_CREDENTIAL" not in caplog.text + state.diagnostics.text("test")
    assert "identity-private" not in caplog.text + state.diagnostics.text("test")
    assert state.diagnostics.snapshot()[-1]["detail"] == {
        "code": "provider_connected", "accounts": 1,
    }


def test_adept_diagnostic_exception_and_error_map_keys_are_not_exported():
    state, _session, _plugin = _rig()
    def fail():
        raise HostileFailure(SIGNED)
    state.kobo_adept_state = fail
    state._session_errors = {"provider.test:" + SECRET: SIGNED}
    result = provider_sessions.provider_diagnostic_state(state)
    assert result["kobo_adept"] == {
        "available": False, "error": "ADEPT indisponible",
    }
    assert result["errors"] == {"connect_failed": 1}
    assert SECRET not in json.dumps(result)
    assert "SECRET_TOKEN" not in json.dumps(result)


def test_provider_diagnostics_never_invokes_plugin_supplied_serializer():
    state, _session, _plugin = _rig()
    def dangerous():
        raise AssertionError("Plugin diagnostic_state() cannot be trusted")
    state._sessions = {
        "provider.test:" + SECRET: NS(
            credential_id=SECRET, connected=True,
            _accounts=[NS(email=SECRET), NS(email=SIGNED)],
            diagnostic_state=dangerous,
        ),
    }
    output = provider_sessions.provider_diagnostic_state(state)
    assert output["sessions"] == [{"connected": True, "accounts": 2}]
    assert SECRET not in json.dumps(output)
    assert "private.invalid" not in json.dumps(output)


def test_provider_diagnostics_discard_malformed_account_structures():
    state, _session, _plugin = _rig()
    state._sessions = {
        "a": NS(credential_id="non-legacy", connected="true",
                _accounts={"secret": SIGNED}),
        "b": NS(credential_id="legacy", connected=True,
                _accounts=[SIGNED]),
    }
    report = provider_sessions.provider_diagnostic_state(state)
    assert report["sessions"] == [{"connected": False, "accounts": 0}]
    assert SIGNED not in json.dumps(report)
