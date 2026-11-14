"""Retired Numilog log schemas must not become Core provider authority (#5054).

Synthetic events only; generic diagnostics remain available through their
canonical provenance-checked contracts, not through provider-specific traces.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from cifereca.diagnostics import Diagnostics

PRIVATE = "https://private.invalid/LOAN/order?token=DO_NOT_PUBLISH"


def _log(path, body):
    path.write_text(
        "2026-10-20 12:43:03,000 WARNING cifereca_numilog "
        "[MainThread] " + body + "\n", encoding="utf-8",
    )


@pytest.mark.parametrize("body", [
    "TRACE numilog session connected=True reason=connect_succeeded",
    "TRACE numilog session connected=False reason=connect_failed",
    "TRACE numilog session connected=False reason=connect_failed account=USER",
    "TRACE numilog session connected=False reason=connect_failed token=" + PRIVATE,
    "TRACE numilog session connected=False reason=PRIVATE_CUSTOMER",
])
def test_retired_session_log_is_never_core_provenance(tmp_path, body):
    log = tmp_path / "cifereca.log"
    _log(log, body)
    report = query_diagnostic_events(Diagnostics(), log_path=log, source="app_log")
    assert report["total"] == 0
    assert report["events"] == []
    serialized = json.dumps(report)
    assert "DO_NOT_PUBLISH" not in serialized
    assert "PRIVATE_CUSTOMER" not in serialized


@pytest.mark.parametrize("operation", [
    "session_state", "acquisition_delivery", "startup_materialization",
])
def test_retired_numilog_core_event_is_not_a_verified_provider_fact(
    tmp_path, operation,
):
    diag = Diagnostics(level="detailed")
    diag.add(
        "NUMILOG", operation, "ok",
        detail={"connected": True, "reason": "connect_succeeded",
                "token": PRIVATE, "account_id": "PRIVATE_CUSTOMER"},
        level="detailed",
    )
    report = query_diagnostic_events(
        diag, log_path=tmp_path / "missing.log", source="core_events",
    )
    assert report["total"] == 1
    row, = report["events"]
    assert "provider_id" not in row
    assert "detail" not in row
    assert row["message"] == "[opération masquée]"
    assert "DO_NOT_PUBLISH" not in json.dumps(report)
    assert "PRIVATE_CUSTOMER" not in json.dumps(report)
