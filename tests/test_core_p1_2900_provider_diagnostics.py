"""#2900 — useful provider provenance without identifying readers or accounts."""
from __future__ import annotations

import json

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.core.diagnostic_source_sync import log_message as source_sync_log
from cifereca.core.public_provider_diagnostics import public_connection_log_message
from cifereca.diagnostics import Diagnostics


def _query(tmp_path, diag, **kwargs):
    return query_diagnostic_events(diag, log_path=tmp_path / "cifereca.log", **kwargs)


def _log(tmp_path, *bodies):
    (tmp_path / "cifereca.log").write_text(
        "".join(
            f"2026-10-23 08:00:0{i},000 ERROR cifereca [MainThread] {body}\n"
            for i, body in enumerate(bodies)
        ),
        encoding="utf-8",
    )


def test_connection_provenance_is_visible_and_filterable_across_both_channels(tmp_path):
    diag = Diagnostics()
    diag.add_core_provider_connect_failure("paris")
    _log(tmp_path, public_connection_log_message({
        "code": "provider_connect_failed", "provider_id": "paris",
    }))
    rows = _query(tmp_path, diag, provider_id="paris")
    assert rows["total"] == 2
    assert {row["source"] for row in rows["events"]} == {"core_events", "app_log"}
    assert all(row["provider_id"] == "paris" for row in rows["events"])
    assert all(row["module"] in {"PLUGIN", "cifereca"} for row in rows["events"])
    assert _query(tmp_path, diag, provider_id="kobo")["total"] == 0
    assert _query(tmp_path, diag, provider_id="paris", module="SYNC")["total"] == 0
    assert _query(tmp_path, diag, provider_id="paris", module="PLUGIN")["total"] == 2


def test_source_sync_retains_provider_not_account_and_does_not_change_module(tmp_path):
    fields = {
        "schema_version": 1, "provider_id": "paris", "operation_kind": "source_sync",
        "phase": "completed", "status": "failed", "code": "source_sync_failed",
        "duration_ms": 25,
    }
    diag = Diagnostics()
    diag.add_core_source_sync(
        "paris", phase="completed", status="failed", code="source_sync_failed",
        duration_ms=25,
    )
    _log(tmp_path, source_sync_log(fields))
    rows = _query(tmp_path, diag, provider_id="paris", module="SYNC")
    assert rows["total"] == 2
    assert all(row["provider_id"] == "paris" for row in rows["events"])
    assert {row["module"] for row in rows["events"]} == {"SYNC", "cifereca"}
    assert all(row["detail"]["code"] == "source_sync_failed" for row in rows["events"])


def test_generic_records_cannot_attest_provider_even_with_valid_payload(tmp_path):
    diag = Diagnostics()
    diag.add("PLUGIN", "provider connect", "error",
             detail={"provider_id": "paris", "code": "provider_connect_failed",
                     "email": "reader@example.test", "password": "PRIVATE"})
    diag.add("SYNC", "source sync", "failed", detail={
        "schema_version": 1, "provider_id": "paris", "operation_kind": "source_sync",
        "phase": "completed", "status": "failed", "code": "source_sync_failed",
    })
    diag.add("KOBO", "automatic backup decision", "ok",
             detail={"status": "partial", "enabled": True})
    _log(tmp_path, "TRACE sync source status=failed provider_id=paris "
         "email=reader@example.test password=PRIVATE")
    rows = _query(tmp_path, diag)
    assert rows["total"] == 4
    assert all("provider_id" not in row for row in rows["events"])
    assert _query(tmp_path, diag, provider_id="paris")["total"] == 0
    assert "reader@example.test" not in json.dumps(rows)
    assert "PRIVATE" not in json.dumps(rows)


@pytest.mark.parametrize("provider", [
    None, 1, True, [], {}, "paris@example.test", "paris/private",
    "account.alice", "account", "token", "paris?access_token=x",
    "A" * 100,
])
def test_provider_filter_rejects_private_or_malformed_identifiers_before_io(
    tmp_path, monkeypatch, provider,
):
    import cifereca.core.diagnostic_event_feed as feed

    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid request read the log")
    monkeypatch.setattr(feed, "_source_logs", forbidden)
    with pytest.raises(ValueError, match="provider"):
        _query(tmp_path, Diagnostics(), provider_id=provider)


def test_provider_filter_works_on_empty_or_partial_channels(tmp_path):
    diag = Diagnostics(enabled=False)
    result = _query(tmp_path, diag, provider_id="paris")
    assert result["events"] == []
    assert result["status"] == "unavailable"
    assert {row["status"] for row in result["sources"]} == {"disabled", "unavailable"}
