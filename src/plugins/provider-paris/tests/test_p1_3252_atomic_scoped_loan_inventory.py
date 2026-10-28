"""#3252/#3122: concurrent Paris borrowers cannot borrow each other's branch evidence.

Synthetic clients only, no credentials, HTTP, ADEPT, borrowing or mutation.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from cifereca.plugin_api import Account, Loan, ProviderConfigurationError
from cifereca_paris import ParisProviderSession


class DiagnosticSink:
    def add(self, *args, **kwargs):
        pass


class LoanClient:
    def __init__(self):
        self.calls = []

    def loans(self, upstream, account_id):
        self.calls.append((upstream, account_id))
        if account_id == "reader-A" and upstream == "DILICOM":
            raise RuntimeError("PRIVATE_UPSTREAM_FAILURE")
        return [Loan(
            id=f"{upstream}:{account_id}",
            provider=upstream,
            medium="digital" if upstream == "DILICOM" else "physical",
            record_id=f"record-{upstream}", title="Synthetic",
            source_account_id=None, identity_id=None,
        )]

    def close(self):
        pass


def fixture():
    session = ParisProviderSession(
        credential_id="synthetic-card", label="Paris",
        credentials={}, options={}, config={}, diagnostics=DiagnosticSink(),
    )
    session.client.close()
    session.client = LoanClient()
    session._enrich_digital_loan_metadata = lambda loan: None
    a = Account(
        display_name="Reader A", unique_id="reader-A", provider_id="paris",
        credential_id="synthetic-card", identity_id="identity-A",
    )
    b = Account(
        display_name="Reader B", unique_id="reader-B", provider_id="paris",
        credential_id="synthetic-card", identity_id="identity-B",
    )
    session.connected = True
    session._accounts = [a, b]
    return session, a, b


def test_snapshot_and_legacy_read_do_not_interleave_branch_failures():
    session, a, b = fixture()
    # Freeze A after loans(a) has written last_loan_errors but BEFORE its
    # snapshot computes per-branch coverage. The B thread attempts to
    # overwrite the same v1 mutable facade by calling legacy loans(b).
    read_a_complete, unblock_a, read_b_started, read_b_complete = (
        Event(), Event(), Event(), Event()
    )
    legacy_loans = session._read_loans_for_account

    def frozen_loans(account, *, publish_legacy_status=True):
        rows = legacy_loans(account, publish_legacy_status=publish_legacy_status)
        if account.unique_id == a.unique_id:
            read_a_complete.set()
            if not unblock_a.wait(10):
                raise AssertionError("Synthetic read A timed out")
        return rows

    session._read_loans_for_account = frozen_loans

    def run_b():
        read_b_started.set()
        rows = session.loans(b)
        read_b_complete.set()
        return rows

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            task_a = pool.submit(session.loan_inventory_snapshot, a)
            assert read_a_complete.wait(10)
            task_b = pool.submit(run_b)
            assert read_b_started.wait(10)
            try:
                # The snapshot must protect BOTH the upstream read and its
                # interpretation of the mutable v1 last_loan_errors.
                assert not read_b_complete.wait(0.15)
            finally:
                unblock_a.set()
            snap_a = task_a.result(timeout=10)
            rows_b = task_b.result(timeout=10)

        assert snap_a.scope.account_id == "reader-A"
        assert snap_a.status == "partial"
        assert snap_a.reason_code == "branch_unavailable"
        assert {(x.upstream, x.completeness) for x in snap_a.branches} == {
            ("dilicom", "partial"), ("syracuse", "unknown"),
        }
        assert len(snap_a.loans) == 1
        assert snap_a.loans[0].source_account_id == "reader-A"
        assert snap_a.loans[0].identity_id == "identity-A"
        assert len(rows_b) == 2
        assert all(row.source_account_id == "reader-B" for row in rows_b)
        assert session.last_loan_errors == []
        assert session.client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
            ("DILICOM", "reader-B"), ("SYRACUSE", "reader-B"),
        ]
    finally:
        unblock_a.set()
        session.close()


def test_same_account_snapshot_is_one_read_per_branch_without_claiming_absence():
    session, a, _b = fixture()
    try:
        snap = session.loan_inventory_snapshot(a)
        assert len(snap.loans) == 1
        assert snap.status == "partial"
        assert session.client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
        assert not any(branch.completeness == "complete" for branch in snap.branches)
    finally:
        session.close()


def test_typed_branch_failure_survives_legacy_side_channel_cleared():
    session, a, _ = fixture()
    original = session._read_loans_for_account

    def clear_v1_after_read(account, *, publish_legacy_status=True):
        rows = original(account, publish_legacy_status=publish_legacy_status)
        session.last_loan_errors.clear()
        return rows

    session.loans = clear_v1_after_read
    try:
        snapshot = session.loan_inventory_snapshot(a)
        assert session.last_loan_errors == []
        assert snapshot.status == "partial"
        assert snapshot.reason_code == "branch_unavailable"
        assert {(branch.upstream, branch.completeness)
                for branch in snapshot.branches} == {
                    ("dilicom", "partial"), ("syracuse", "unknown"),
                }
        assert len(snapshot.loans) == 1
        assert session.client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        session.close()


def test_typed_success_ignores_stale_legacy_failure_from_another_read():
    session, _a, b = fixture()
    original = session._read_loans_for_account

    def inject_v1_error_after_read(account, *, publish_legacy_status=True):
        rows = original(account, publish_legacy_status=publish_legacy_status)
        session.last_loan_errors = [{"upstream": "DILICOM", "code": "stale"}]
        return rows

    session.loans = inject_v1_error_after_read
    try:
        snapshot = session.loan_inventory_snapshot(b)
        assert snapshot.status == "success"
        assert snapshot.reason_code is None
        assert len(snapshot.loans) == 2
        assert all(branch.completeness == "unknown" for branch in snapshot.branches)
        assert session.client.calls == [
            ("DILICOM", "reader-B"), ("SYRACUSE", "reader-B"),
        ]
    finally:
        session.close()


def test_typed_reader_without_scoped_branch_evidence_fails_closed():
    session, a, _ = fixture()
    session._read_loans_for_account = (
        lambda _account, *, publish_legacy_status=False: []
    )  # Bad typed producer: no per-branch GET/evidence.
    try:
        with pytest.raises(ProviderConfigurationError, match="loan_inventory_evidence_missing"):
            session.loan_inventory_snapshot(a)
        assert session.client.calls == []
    finally:
        session.close()
