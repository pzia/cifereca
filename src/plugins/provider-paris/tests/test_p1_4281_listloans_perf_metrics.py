"""#4281: Paris loan latency evidence without extra requests or right changes.

All borrowers, titles, clients and clocks are synthetic; no real Paris access.
The timing events are observational, never a coverage or borrow certificate.
"""
from __future__ import annotations

import pytest

from cifereca.plugin_api import Cancelled
from cifereca_paris import ParisProviderSession
from tests.test_p1_3252_atomic_scoped_loan_inventory import fixture


class Capture:
    def __init__(self):
        self.events = []

    def add(self, category, operation, status="ok", duration_ms=None,
            detail=None, level="normal"):
        self.events.append({
            "category": category, "operation": operation, "status": status,
            "duration_ms": duration_ms, "detail": detail, "level": level,
        })

    def perf(self):
        return [event for event in self.events if event["category"] == "PERF"]


def test_listloans_timing_observes_both_branches_once_without_network_expansion():
    session, _alice, bob = fixture()
    diag = session.diagnostics = Capture()
    try:
        snapshot = session.loan_inventory_snapshot(bob)
        assert snapshot.status == "success"
        assert session.client.calls == [
            ("DILICOM", "reader-B"), ("SYRACUSE", "reader-B"),
        ]
        branch_a, branch_b, total = diag.perf()
        assert [event["operation"] for event in diag.perf()] == [
            "Paris ListLoans", "Paris ListLoans", "Paris loan inventory",
        ]
        assert [event["detail"]["upstream"] for event in (branch_a, branch_b)] == [
            "DILICOM", "SYRACUSE",
        ]
        assert [event["detail"]["outcome"] for event in (branch_a, branch_b)] == [
            "returned", "returned",
        ]
        assert [event["detail"]["rows"] for event in (branch_a, branch_b)] == [1, 1]
        assert total["detail"] == {
            "provider": "paris", "branches_attempted": 2,
            "branches_failed": 0, "loans": 2,
            "ebooks_search_calls": 0, "coverage_attested": False,
        }
        assert all(
            type(row["duration_ms"]) is int
            and 0 <= row["duration_ms"] <= 600_000
            and row["level"] == "detailed"
            for row in diag.perf()
        )
        # Diagnostics carry no borrower coordinate, provider query or loan ID.
        assert all(
            "reader-B" not in str(row) and "synthetic-card" not in str(row)
            for row in diag.perf()
        )
    finally:
        session.close()


def test_failed_dilicom_branch_is_measured_without_hiding_syracuse_or_claiming_full():
    session, alice, _bob = fixture()
    diag = session.diagnostics = Capture()
    try:
        snapshot = session.loan_inventory_snapshot(alice)
        assert snapshot.status == "partial"
        assert session.client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
        branch_a, branch_b, total = diag.perf()
        assert (branch_a["status"], branch_a["detail"]["outcome"],
                branch_a["detail"]["rows"]) == ("error", "failed", 0)
        assert branch_b["detail"]["outcome"] == "returned"
        assert total["status"] == "warning"
        assert total["detail"]["branches_failed"] == 1
        assert total["detail"]["coverage_attested"] is False
    finally:
        session.close()


def test_cooperative_cancel_records_only_attempted_branch_not_fake_inventory():
    session, _alice, bob = fixture()
    diag = session.diagnostics = Capture()
    calls = []
    def cancel(upstream, borrower_id):
        calls.append((upstream, borrower_id))
        raise Cancelled("synthetic cancellation")
    session.client.loans = cancel
    try:
        with pytest.raises(Cancelled):
            session.loan_inventory_snapshot(bob)
        assert calls == [("DILICOM", "reader-B")]
        events = diag.perf()
        assert len(events) == 1
        assert events[0]["detail"]["outcome"] == "cancelled"
        assert events[0]["status"] == "warning"
    finally:
        session.close()


def test_ebooks_queries_and_cache_warmth_are_counted_not_reissued():
    session, _alice, bob = fixture()
    diag = session.diagnostics = Capture()
    session._enrich_digital_loan_metadata = (
        ParisProviderSession._enrich_digital_loan_metadata.__get__(
            session, ParisProviderSession,
        )
    )
    searches = []
    def search(query, *, scenario, result_size):
        assert scenario == "EBOOKS" and result_size == 12
        searches.append(query)
        return []
    session.client.search_catalog = search
    try:
        cold = session.loan_inventory_snapshot(bob)
        assert cold.status == "success"
        assert searches == ["record-DILICOM", "Synthetic"]
        cold_metrics = diag.perf()[-1]
        assert cold_metrics["detail"]["ebooks_search_calls"] == 2
        assert cold_metrics["detail"]["coverage_attested"] is False
        warm = session.loan_inventory_snapshot(bob)
        assert warm.status == "success"
        assert searches == ["record-DILICOM", "Synthetic"]
        warm_metrics = diag.perf()[-1]
        assert warm_metrics["detail"]["ebooks_search_calls"] == 0
        assert len(session.client.calls) == 4  # Fresh ListLoans each time.
        assert [call[0] for call in session.client.calls] == [
            "DILICOM", "SYRACUSE", "DILICOM", "SYRACUSE",
        ]
    finally:
        session.close()


def test_diagnostics_failure_never_changes_loan_inventory_or_adds_requests():
    session, _alice, bob = fixture()
    class BrokenSink:
        def add(self, *_args, **_kwargs):
            raise OSError("simulated diagnostic unavailable")
    session.diagnostics = BrokenSink()
    try:
        snapshot = session.loan_inventory_snapshot(bob)
        assert snapshot.status == "success"
        assert len(snapshot.loans) == 2
        assert session.client.calls == [
            ("DILICOM", "reader-B"), ("SYRACUSE", "reader-B"),
        ]
    finally:
        session.close()
