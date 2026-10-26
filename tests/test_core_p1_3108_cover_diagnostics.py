"""Offline Core #3108: cover failures are observations, not EPUB downloads."""
from __future__ import annotations

import json
import logging

import pytest

from cifereca.diagnostics import Diagnostics
from cifereca.core.diagnostic_cover_failure import LABELS, public_fields, public_message
from cifereca.core.diagnostic_event_feed import _source_diagnostics


def test_twelve_cover_http_failures_are_twelve_attested_observations(caplog):
    diagnostics = Diagnostics(level="normal")
    with caplog.at_level(logging.DEBUG, logger="cifereca"):
        for index in range(12):
            diagnostics.add_core_cover_failure("http_status", duration_ms=index)
    snapshot = diagnostics.snapshot()
    assert len(snapshot) == 12
    assert all(r["verified_cover_failure"] is True for r in snapshot)
    rows, state = _source_diagnostics(diagnostics)
    assert state["status"] == "available"
    assert len(rows) == 12
    for row in rows:
        assert row["source"] == "core_events"
        assert row["module"] == "COVER"
        assert row["severity"] == "error"
        assert row["message"] == "couverture · échec HTTP"
        assert row["detail"] == {"code": "http_status"}
        assert not {"provider_id", "task_ref", "http_status_code", "url"} & set(row)
    # Exactly one physical Core diagnostic per failure in the canonical log.
    journal = [r.getMessage() for r in caplog.records
               if r.getMessage().startswith("couverture · échec HTTP")]
    assert len(journal) == 12
    raw = [r for r in caplog.records
           if r.getMessage() == "couverture · échec HTTP"]
    assert all(getattr(r, "_cifereca_journal_payload")["operation"]
               == "cover_download_failure" for r in raw)
    assert all(getattr(r, "_cifereca_journal_payload")["detail"]
               == {"code": "http_status"} for r in raw)
    # A log row and its compatibility snapshot are two views, not 24 GETs.
    assert not any("provider_id" in r.__dict__ for r in raw)


@pytest.mark.parametrize("reason", list(LABELS))
def test_closed_cover_failure_reason_renders_distinctly(reason):
    row = public_fields({"code": reason, "duration_ms": 5})
    assert row == {"code": reason, "duration_ms": 5}
    assert public_message(row) == LABELS[reason]


@pytest.mark.parametrize("payload", [
    {"code": "http_status", "duration_ms": True},
    {"code": "http_status", "duration_ms": -1},
    {"code": "http_status", "duration_ms": 600001},
    {"code": "403", "duration_ms": 1},
    {"code": object(), "duration_ms": 1},
])
def test_invalid_observations_never_claim_a_cover_reason(payload, caplog):
    diagnostics = Diagnostics(level="detailed")
    with caplog.at_level(logging.DEBUG, logger="cifereca"):
        diagnostics.add_core_cover_failure(payload["code"],
                                           duration_ms=payload["duration_ms"])
    assert public_fields(payload) == {}
    assert diagnostics.snapshot() == []
    assert "couverture ·" not in caplog.text


def test_unknown_keys_cannot_extend_cover_schema():
    assert public_fields({"code": "http_status", "duration_ms": 1, "url": "PRIVATE"}) == {}


def test_generic_cover_diagnostic_cannot_attest_http_failure():
    diagnostics = Diagnostics(level="detailed")
    diagnostics.add("COVER", "download", "error", duration_ms=4,
                    detail={"code": "http_status"}, level="detailed")
    events, _ = _source_diagnostics(diagnostics)
    assert len(events) == 1
    assert events[0]["module"] == "COVER"
    assert events[0]["message"] != "couverture · échec HTTP"
    assert "detail" not in events[0]
    assert diagnostics.snapshot()[0].get("verified_cover_failure") is None


def test_failed_cover_classification_never_exports_hostile_details():
    diagnostics = Diagnostics(level="normal")
    diagnostics.add_core_cover_failure("timeout", duration_ms=8)
    diagnostics._items[-1].detail["account"] = "PRIVATE_account"
    rows, _ = _source_diagnostics(diagnostics)
    assert len(rows) == 1
    assert "PRIVATE_account" not in repr(rows)
    assert rows[0].get("detail") is None
    assert rows[0]["message"] != "couverture · délai dépassé"
