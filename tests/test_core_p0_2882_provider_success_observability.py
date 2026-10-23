"""P0 #2882: provider connect SUCCESS is attributable without borrower data."""
from __future__ import annotations

import json
import logging

import pytest

from cifereca.core import provider_sessions
from cifereca.core.diagnostic_event_feed import _source_diagnostics, query_diagnostic_events
from cifereca.core.public_provider_diagnostics import (
    public_connection_success_fields, public_connection_success_log_fields,
    public_connection_success_log_message,
)
from cifereca.diagnostics import Diagnostics
from tests.test_core_p0_1143_provider_connect_privacy import _rig, SECRET


@pytest.mark.parametrize("singleton", [False, True])
def test_success_has_attested_provider_both_channels_and_no_private_identifiers(
    singleton, caplog, tmp_path,
):
    service, session, _ = _rig(singleton=singleton, succeeds=True)
    logger = logging.getLogger("cifereca")
    with caplog.at_level(logging.INFO, logger="cifereca"):
        result = provider_sessions.connect_configured(service, logger=logger)
    assert len(result["connected"]) == 1
    assert not result["errors"]
    rows, _ = _source_diagnostics(service.diagnostics)
    typed = [r for r in rows if r.get("detail", {}).get("code") == "provider_connected"]
    assert len(typed) == 1
    assert typed[0]["provider_id"] == "provider.test"
    assert typed[0]["severity"] == "info"
    assert typed[0]["status"] == "ok"
    assert typed[0]["detail"] == {
        "provider_id": "provider.test", "code": "provider_connected",
        "source_kind": "singleton_public" if singleton else "configured",
        "account_count": 0 if singleton else 1,
    }
    messages = [
        record.getMessage() for record in caplog.records
        if record.getMessage().startswith("EVENT provider connection success ")
    ]
    assert len(messages) == 1
    assert public_connection_success_log_fields(messages[0]) == typed[0]["detail"]
    path = tmp_path / "cifereca.log"
    path.write_text(
        "2026-10-23 10:00:00,000 INFO cifereca [MainThread] " + messages[0] + "\n",
        encoding="utf-8",
    )
    feed = query_diagnostic_events(
        service.diagnostics, log_path=path, provider_id="provider.test",
    )
    good = [r for r in feed["events"] if r.get("detail", {}).get("code")
            == "provider_connected"]
    assert len(good) == 2
    assert {r["source"] for r in good} == {"core_events", "app_log"}
    assert {r["module"] for r in good} == {"PLUGIN", "cifereca"}
    assert query_diagnostic_events(
        service.diagnostics, log_path=path, provider_id="paris",
    )["total"] == 0
    publication = json.dumps(feed) + caplog.text
    for secret in (SECRET, "identity-private", "Private Label", "SECRET_TOKEN"):
        assert secret not in publication


@pytest.mark.parametrize("invalid", [
    {"provider_id": "reader@example.test", "code": "provider_connected",
     "source_kind": "configured", "account_count": 1},
    {"provider_id": "provider.test", "code": "provider_connected",
     "source_kind": "configured", "account_count": True},
    {"provider_id": "provider.test", "code": "provider_connected",
     "source_kind": "configured", "account_count": -1},
    {"provider_id": "provider.test", "code": "provider_connected",
     "source_kind": "configured", "account_count": 10001},
    {"provider_id": "provider.test", "code": "provider_connected",
     "source_kind": "private", "account_count": 1},
    {"provider_id": "provider.test", "code": "provider_connected",
     "source_kind": "configured", "account_count": 1,
     "credential_id": "PRIVATE"},
    {"provider_id": "provider.test", "code": "provider_connect_failed",
     "source_kind": "configured", "account_count": 1},
])
def test_success_schema_rejects_private_and_noncanonical_fields(invalid):
    assert public_connection_success_fields(invalid) == {}
    assert public_connection_success_log_message(invalid) is None


def test_log_reader_does_not_accept_extended_or_noncanonical_success_text(tmp_path):
    fields = {
        "provider_id": "paris", "code": "provider_connected",
        "source_kind": "configured", "account_count": 1,
    }
    valid = public_connection_success_log_message(fields)
    assert public_connection_success_log_fields(valid) == fields
    assert public_connection_success_log_fields(
        valid + " email=reader@example.test"
    ) == {}
    assert public_connection_success_log_fields(
        valid.replace('"account_count":1', '"account_count":1,"account_id":"PRIVATE"')
    ) == {}
    assert public_connection_success_log_fields(
        valid.replace('"code":"provider_connected"', '"code":"provider_connect_failed"')
    ) == {}
    assert public_connection_success_log_fields(
        valid.replace(',"code":', ', "code":')
    ) == {}
    bad = "2026-10-23 10:00:00,000 INFO cifereca [Thread] " + valid + " email=PRIVATE"
    (tmp_path / "cifereca.log").write_text(bad + "\n", encoding="utf-8")
    events = query_diagnostic_events(
        Diagnostics(), log_path=tmp_path / "cifereca.log", source="app_log",
    )["events"]
    assert len(events) == 1
    assert events[0]["message"].startswith("[contenu libre masqué")
    assert "provider_id" not in events[0]
    assert "PRIVATE" not in json.dumps(events)


def test_generic_plugin_success_cannot_claim_attestation(tmp_path):
    diag = Diagnostics()
    diag.add("PLUGIN", "provider connected", "ok", detail={
        "provider_id": "paris", "code": "provider_connected",
        "source_kind": "configured", "account_count": 1,
    })
    rows, _ = _source_diagnostics(diag)
    assert len(rows) == 1
    assert not rows[0].get("detail")
    assert "provider_id" not in rows[0]
    assert query_diagnostic_events(
        diag, log_path=tmp_path / "missing", provider_id="paris",
    )["total"] == 0


def test_errors_mode_and_disabled_collector_do_not_invent_success(caplog):
    errors = Diagnostics(enabled=True, level="errors")
    errors.add_core_provider_connect_success("paris", account_count=1)
    assert _source_diagnostics(errors)[0] == []
    disabled = Diagnostics(enabled=False)
    with caplog.at_level(logging.INFO, logger="cifereca"):
        disabled.add_core_provider_connect_success("paris", account_count=1)
    assert _source_diagnostics(disabled)[0] == []
    assert "EVENT provider connection success " in caplog.text


def test_unregistered_provider_cannot_fake_success_via_configured_profile(caplog):
    service, session, plugin = _rig(succeeds=True)
    service.registry.maybe_get = lambda _slug: None
    with caplog.at_level(logging.INFO, logger="cifereca"):
        provider_sessions.connect_configured(service, logger=logging.getLogger("cifereca"))
    assert "EVENT provider connection success " not in caplog.text
    assert not any(row.get("verified_public_provider_success")
                   for row in service.diagnostics.snapshot())


def test_failed_connection_does_not_emit_success(caplog):
    service, session, plugin = _rig()
    with caplog.at_level(logging.INFO, logger="cifereca"):
        provider_sessions.connect_configured(service, logger=logging.getLogger("cifereca"))
    assert "EVENT provider connection success " not in caplog.text
    rows, _ = _source_diagnostics(service.diagnostics)
    assert not [r for r in rows if r.get("detail", {}).get("code") == "provider_connected"]
