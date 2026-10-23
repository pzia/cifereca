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
    expected_reserve = min(90, expected_remaining / 2)
    assert kwargs["limits"].seconds == pytest.approx(
        expected_remaining - expected_reserve
    )
    assert 0 < kwargs["limits"].seconds < kwargs["seconds"] <= 600
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


def test_checkpoint_preflight_time_is_charged_to_same_scoped_envelope(
    scoped, monkeypatch,
):
    service, _db, _root = scoped
    ticks = [0.0]
    real_begin = broker.begin_inventory_resume

    def costly_preflight(*args, **kwargs):
        value = real_begin(*args, **kwargs)
        ticks[0] += 75.0
        return value

    monkeypatch.setattr(broker, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(broker, "begin_inventory_resume", costly_preflight)
    with patch.object(broker, "audit_library", wraps=audit_library) as spy:
        result = broker.LibraryReconciliationReadBrokerImpl(service).audit(
            identity_id="alice",
        )
    assert result["status"] == "complete"
    options = spy.call_args.kwargs
    # No second allocation of the 75 s consumed after the SQLite snapshot.
    assert options["seconds"] == pytest.approx(525)
    assert options["limits"].seconds == pytest.approx(435)
    assert options["comparison_seconds"] == 30
    assert options["candidate_seconds"] == 60


def test_exhausted_checkpoint_preflight_never_begins_or_claims_complete_inventory(
    scoped, monkeypatch,
):
    service, _db, _root = scoped
    clock = [0.0]
    begin = broker.begin_inventory_resume

    def exhaust(*args, **kwargs):
        slot = begin(*args, **kwargs)
        clock[0] += 600
        return slot

    monkeypatch.setattr(broker, "monotonic", lambda: clock[0])
    monkeypatch.setattr(broker, "begin_inventory_resume", exhaust)
    with patch.object(broker, "audit_library",
                      side_effect=AssertionError("cannot start after deadline")) as spy:
        result = broker.LibraryReconciliationReadBrokerImpl(service).audit(
            identity_id="alice",
        )
    spy.assert_not_called()
    assert result["status"] == "incomplete_scan"
    assert result["reasons"] == ["audit_time_budget"]
    assert result["repair_authorized"] is False
    assert result["acquisition_authorized"] is False
    assert "PRIVATE" not in json.dumps(result)


def test_partial_file_scan_still_runs_right_and_metadata_comparisons(
    scoped, monkeypatch,
):
    from cifereca.core import library_audit as audit_core
    from cifereca.core.library_inventory import InventoryLimits
    from cifereca.core.library_reconciliation import read_library_snapshot

    _service, database, root = scoped
    clock = [0.0]
    scan = audit_core.scan_identity_roots

    def scan_using_reserved_budget(*args, **kwargs):
        assert kwargs["limits"].seconds == 510
        observation = scan(*args, **kwargs)
        # Model an inventory reaching its local budget while only a subset
        # of the root was examined. Never infer missing rights from this.
        observation.reasons.add("time_budget")
        clock[0] += 510.0
        return observation

    monkeypatch.setattr(audit_core, "monotonic", lambda: clock[0])
    snapshot = read_library_snapshot(database)
    with patch.object(audit_core, "scan_identity_roots",
                      side_effect=scan_using_reserved_budget), patch.object(
            audit_core, "reconcile", wraps=audit_core.reconcile,
        ) as compare, patch.object(
            audit_core, "metadata_candidates",
            wraps=audit_core.metadata_candidates,
        ) as bibliography:
        report = audit_core.audit_library(
            database, {"alice": (root,)}, seconds=600,
            limits=InventoryLimits(seconds=510),
            comparison_seconds=30, candidate_seconds=60,
            candidate_index_seconds=30, _snapshot=snapshot,
        )
    assert compare.call_count == bibliography.call_count == 1
    assert report.redacted()["status"] == "incomplete_scan"
    assert "time_budget" in report.inventory.reasons
    assert report.reconciliation.files  # observed files remain comparable
    assert report.metadata_candidates.match_phase_executed is True
    assert clock == [510.0]
