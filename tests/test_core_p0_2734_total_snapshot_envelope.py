"""P0 #2734: a scoped 600s audit includes the initial SQLite/WAL read.

Synthetic fixtures and deterministic clocks; no real slow reads, private
paths in diagnostics, provider network or library mutations.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cifereca.core import library_audit_broker as broker
from cifereca.core.library_audit import audit_library
from tests.test_library_audit_broker import scoped


@pytest.mark.parametrize("snapshot_seconds,expected_remaining", [
    (0.0, 600.0),
    (8.0, 592.0),
    (37.5, 562.5),
    (590.0, 10.0),
])
def test_one_budget_is_shared_between_sqlite_wal_and_subsequent_audit(
    scoped, monkeypatch, snapshot_seconds, expected_remaining,
):
    service, _db, _root = scoped
    clock = [0.0]
    original = broker.read_library_snapshot

    def read_with_cost(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        clock[0] += snapshot_seconds
        return snapshot

    monkeypatch.setattr(broker, "monotonic", lambda: clock[0])
    monkeypatch.setattr(broker, "read_library_snapshot", read_with_cost)
    with patch.object(broker, "audit_library", wraps=audit_library) as audit_spy:
        result = broker.LibraryReconciliationReadBrokerImpl(service).audit(
            identity_id="alice",
        )
    assert result["status"] == "complete"
    assert audit_spy.call_count == 1
    kwargs = audit_spy.call_args.kwargs
    assert kwargs["seconds"] == pytest.approx(expected_remaining)
    assert kwargs["limits"].seconds == pytest.approx(expected_remaining)
    assert kwargs["comparison_seconds"] == 30
    assert kwargs["candidate_seconds"] == 60
    assert kwargs["candidate_index_seconds"] == 30
    # The public result must never include the private paths or SHA/ISBN.
    output = json.dumps(result)
    assert "PRIVATE" not in output
    assert str(_root) not in output


@pytest.mark.parametrize("snapshot_seconds", [600.0, 601.0])
def test_exhausted_envelope_never_claims_completed_scan(
    scoped, monkeypatch, snapshot_seconds,
):
    service, _db, _root = scoped
    clock = [0.0]
    original = broker.read_library_snapshot

    def read_too_slow(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] += snapshot_seconds
        return result

    monkeypatch.setattr(broker, "monotonic", lambda: clock[0])
    monkeypatch.setattr(broker, "read_library_snapshot", read_too_slow)
    with patch.object(broker, "audit_library",
                      side_effect=AssertionError("inventory must not start")) as audited:
        summary = broker.LibraryReconciliationReadBrokerImpl(service).audit(
            identity_id="alice",
        )
    audited.assert_not_called()
    assert summary["status"] == "incomplete_scan"
    assert summary["reasons"] == ["audit_time_budget"]
    assert summary["repair_authorized"] is False
    assert summary["acquisition_authorized"] is False
    assert summary["read_only"] is True
    assert summary["snapshot_ms"] == 30000  # bounded, aggregate only
    assert "PRIVATE" not in json.dumps(summary)
    assert str(_db) not in json.dumps(summary)
