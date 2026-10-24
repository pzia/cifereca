"""P0 #3101 — provider connection requests, NOT raw HTTP authentications.

All sessions are in-memory fakes; no BnF-Prêt plugin, provider network,
private identity, credential, signed URL, or source account is published.
"""
from __future__ import annotations

from dataclasses import replace
import json
import logging
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core import provider_sessions
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_log_policy import is_public_core_observation
from cifereca.core.diagnostic_provider_connection_attempt import (
    ConnectionAttempt, log_fields, log_message, new_attempt, public_fields,
)
from cifereca.diagnostics import Diagnostics
from tests.test_core_p0_1143_provider_connect_privacy import _rig

PRIVATE = "SECRET_CONNECTED_ACCOUNT_2026"
PRIVATE_URL = "https://private.invalid/LOAN/secret.acsm?token=PRIVATE"


def _registry():
    return NS(maybe_get=lambda name: NS(descriptor=NS(id=name)))


def _attempt(diagnostics, *, identity="alice", credential="paris-one", provider="paris"):
    return new_attempt(
        _registry(), provider, diagnostics=diagnostics,
        identity_id=identity, credential_id=credential,
        source_kind="configured",
    )


def _rows(diagnostics):
    return [
        r["detail"] for r in diagnostics.snapshot()
        if r["operation"] == "provider_connect"
    ]


def test_same_scope_is_pseudonymous_but_distinct_requests_get_distinct_refs():
    diag = Diagnostics(level="normal")
    first = _attempt(diag)
    second = _attempt(diag)
    other_credential = _attempt(diag, credential="paris-two")
    other_identity = _attempt(diag, identity="bob")
    assert first.operation_ref != second.operation_ref
    assert first.scope_ref == second.scope_ref
    assert len({first.scope_ref, other_credential.scope_ref, other_identity.scope_ref}) == 3
    assert first.scope_ref not in {"alice", "paris-one"}
    assert len(first.scope_ref) == 24
    assert _rows(diag)[0]["network_auth"] == "unknown"
    assert not any("task_ref" in row for row in _rows(diag))
    assert PRIVATE not in repr(_rows(diag))


def test_canonical_log_rejects_nearly_valid_private_malformations():
    diag = Diagnostics()
    attempt = _attempt(diag)
    original = _rows(diag)[0]
    message = log_message(original)
    assert message is not None and log_fields(message) == original
    assert is_public_core_observation("cifereca", message)
    assert not is_public_core_observation("cifereca.plugin", message)
    malformed = [
        {**original, "credential_id": PRIVATE},
        {**original, "phase": "terminal", "status": "success", "reason_code": "connect_failed"},
        {**original, "phase": "session_reused", "status": "failed", "reason_code": "session_reused"},
        {**original, "operation_ref": PRIVATE},
        {**original, "scope_ref": PRIVATE},
        {**original, "network_auth": "true"},
        {**original, "duration_ms": -1},
        {**original, "duration_ms": True},
        {**original, "reason_code": PRIVATE_URL},
        {**original, "task_ref": "ffffffffff"},
    ]
    for candidate in malformed:
        assert public_fields(candidate) == {}
        assert log_message(candidate) is None
    assert not log_fields(message + " " + PRIVATE)
    assert not log_fields(message.replace('"requested"', '"requested", "token": "PRIVATE"', 1))


def test_one_request_joins_exact_log_and_core_events_without_timestamp_join(tmp_path):
    diag = Diagnostics()
    attempt = _attempt(diag)
    attempt.emit(diag, "connect_invoked", "unknown", "connect_invoked")
    attempt.emit(diag, "terminal", "success", "connected")
    published = _rows(diag)
    assert [x["phase"] for x in published] == [
        "requested", "connect_invoked", "terminal",
    ]
    assert {x["operation_ref"] for x in published} == {attempt.operation_ref}
    log = tmp_path / "cifereca.log"
    log.write_text("".join(
        f"2026-10-24 17:11:33,{n:03d} INFO cifereca {log_message(row)}\n"
        for n, row in enumerate(published)
    ), encoding="utf-8")
    report = query_diagnostic_events(diag, log_path=log, provider_id="paris", limit=30)
    events = [x for x in report["events"]
              if x.get("detail", {}).get("operation_ref") == attempt.operation_ref]
    assert len(events) == 6  # three real phases, mirrored into two channels
    assert {x["source"] for x in events} == {"app_log", "core_events"}
    assert {x["provider_id"] for x in events} == {"paris"}
    assert {x["detail"]["network_auth"] for x in events} == {"unknown"}
    assert not any(x.get("task_ref") for x in events)
    # Each event_id is unique per physical record, but never a join key.
    assert len({x["event_id"] for x in events}) == 6


@pytest.mark.parametrize("succeeds", [False, True])
def test_real_configured_orchestrator_observes_one_request_and_one_invocation(succeeds, caplog):
    service, session, plugin = _rig(succeeds=succeeds)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        provider_sessions.connect_configured(service, logger=logging.getLogger("cifereca"))
    records = _rows(service.diagnostics)
    assert [row["phase"] for row in records] == ["requested", "connect_invoked", "terminal"]
    assert len({row["operation_ref"] for row in records}) == 1
    assert len({row["scope_ref"] for row in records}) == 1
    assert records[-1]["status"] == ("success" if succeeds else "failed")
    assert session.connect.call_count == 1
    assert all(row["source_kind"] == "configured" for row in records)
    assert all(row["caller_category"] == "other" for row in records)
    assert all(row["network_auth"] == "unknown" for row in records)
    output = caplog.text + service.diagnostics.text("support")
    assert "PRIVATE_ACCOUNT_CREDENTIAL" not in output
    assert "private.invalid" not in output


def test_two_credentials_one_provider_emit_two_independent_attempts():
    service, _session, plugin = _rig(succeeds=True)
    service.config.provider_accounts = lambda **kwargs: [
        {"provider": "provider.test", "id": "PRIVATE_FIRST", "identity_id": "alice",
         "enabled": True, "credentials": {}},
        {"provider": "provider.test", "id": "PRIVATE_SECOND", "identity_id": "bob",
         "enabled": True, "credentials": {}},
    ]
    calls = []
    def new_session(**kwargs):
        credential = kwargs["credential_id"]
        obj = NS(
            connected=False, close=Mock(),
            provider_id="provider.test", credential_id=credential,
        )
        def connect():
            calls.append(credential)
            obj.connected = True
            return [NS(unique_id="", is_primary=True, display_name="private",
                       barcode="", provider_id="provider.test", credential_id=credential)]
        obj.connect = connect
        return obj
    plugin.create_session = new_session
    provider_sessions.connect_configured(service, logger=logging.getLogger("cifereca"))
    events = _rows(service.diagnostics)
    assert len(events) == 6
    assert len({x["operation_ref"] for x in events}) == 2
    assert len({x["scope_ref"] for x in events}) == 2
    assert len(calls) == 2
    assert "PRIVATE_FIRST" not in json.dumps(events)
    assert "PRIVATE_SECOND" not in json.dumps(events)


def test_missing_plugin_request_terminates_without_invocation():
    service, session, plugin = _rig()
    service.registry.maybe_get = lambda name: None
    provider_sessions.connect_configured(service, logger=logging.getLogger("cifereca"))
    events = _rows(service.diagnostics)
    assert [e["phase"] for e in events] == ["requested", "terminal"]
    assert events[-1]["status"] == "skipped"
    assert events[-1]["reason_code"] == "plugin_missing"
    assert "scope_ref" not in events[-1]
    session.connect.assert_not_called()


def test_generic_diagnostics_cannot_claim_correlated_operation(tmp_path):
    d = Diagnostics()
    attempt = _attempt(d)
    d.clear()
    fields = {
        "provider_id": "paris", "operation_ref": attempt.operation_ref,
        "phase": "terminal", "status": "success", "reason_code": "connected",
        "caller_category": "other", "source_kind": "configured",
        "network_auth": "unknown", "duration_ms": 2,
    }
    d.add("PLUGIN", "provider_connect", "ok", detail=fields)
    rows = query_diagnostic_events(d, log_path=tmp_path / "missing", source="core_events")["events"]
    assert len(rows) == 1
    assert "operation_ref" not in rows[0].get("detail", {})
    assert "provider_id" not in rows[0]


def _preview_rig():
    diagnostics = Diagnostics()
    real = NS(
        connected=False, provider_id="kobo", credential_id="SECRET_KOBO",
        capabilities=NS(backup=True), close=Mock(),
    )
    def connect():
        real.connected = True
    real.connect = Mock(side_effect=connect)
    plugin = NS(descriptor=NS(id="kobo", state="ready", display_name="Kobo"),
                create_session=Mock(return_value=real))
    storage = NS(identity_for_connection=lambda provider, credential:
                 "SECRET_IDENTITY" if (provider, credential) == ("kobo", "SECRET_KOBO")
                 else None)
    service = NS(
        storage=storage,
        config=NS(provider_accounts=lambda **kw: [{
            "provider": "kobo", "id": "SECRET_KOBO",
            "identity_id": "SECRET_IDENTITY", "enabled": True,
            "credentials": {"password": PRIVATE},
        }]),
        registry=NS(maybe_get=lambda provider: plugin),
        _sessions={},
        _session_key=lambda p, c: p + ":" + c,
        diagnostics=diagnostics,
    )
    return service, real, plugin


def test_kobo_preview_first_connect_then_real_session_reuse_with_distinct_refs():
    service, session, plugin = _preview_rig()
    first = provider_sessions.connect_kobo_preview(service, "SECRET_KOBO")
    second = provider_sessions.connect_kobo_preview(service, "SECRET_KOBO")
    assert first is second is session
    assert session.connect.call_count == 1
    assert plugin.create_session.call_count == 1
    events = _rows(service.diagnostics)
    assert [e["phase"] for e in events] == [
        "requested", "connect_invoked", "terminal",
        "requested", "session_reused", "terminal",
    ]
    assert [e["source_kind"] for e in events] == ["preview"] * 6
    assert len({e["operation_ref"] for e in events}) == 2
    assert len({e["scope_ref"] for e in events}) == 1
    assert all(e["caller_category"] == "explicit_refresh" for e in events)
    assert all(e["network_auth"] == "unknown" for e in events)
    assert PRIVATE not in repr(events)
    assert "SECRET_IDENTITY" not in repr(events)
    assert "SECRET_KOBO" not in repr(events)


def test_kobo_preview_rejects_stale_reused_session_as_failed_request():
    service, session, plugin = _preview_rig()
    stale = NS(connected=True, provider_id="paris", credential_id="SECRET_KOBO")
    service._sessions["kobo:SECRET_KOBO"] = stale
    with pytest.raises(Exception, match="Session Kobo différente"):
        provider_sessions.connect_kobo_preview(service, "SECRET_KOBO")
    assert [e["phase"] for e in _rows(service.diagnostics)] == [
        "requested", "terminal",
    ]
    assert _rows(service.diagnostics)[-1]["reason_code"] == "session_invalid"
    assert _rows(service.diagnostics)[-1]["status"] == "failed"
    session.connect.assert_not_called()


def test_scope_ref_rotates_with_process_secret_without_exposing_stable_hash(monkeypatch):
    from cifereca.core import diagnostic_provider_connection_attempt as trace
    first = trace._connection_scope("PRIVATE_IDENTITY", "paris", "PRIVATE_CREDENTIAL")
    monkeypatch.setattr(trace, "_PROCESS_SECRET", b"synthetic-other-process-secret-32"[:32])
    next_process = trace._connection_scope("PRIVATE_IDENTITY", "paris", "PRIVATE_CREDENTIAL")
    assert first is not None and next_process is not None
    assert first != next_process
    assert "PRIVATE" not in first + next_process


def test_disabled_diagnostics_does_not_suppress_canonical_log(caplog):
    disabled = Diagnostics(enabled=False)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        attempt = _attempt(disabled)
        attempt.emit(disabled, "terminal", "failed", "connect_failed")
    assert _rows(disabled) == []
    body = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("EVENT provider connection attempt ")
    ]
    assert len(body) == 2
    assert [log_fields(message)["phase"] for message in body] == [
        "requested", "terminal",
    ]
    assert all("task_ref" not in log_fields(message) for message in body)
