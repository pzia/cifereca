"""#3628 Paris: typed two-branch inventory does not read/write legacy error state.

Real ParisProviderSession; synthetic DILICOM/SYRACUSE clients, no network,
return, ADEPT, acquisition or Core mutations.
"""
import pytest

from cifereca.plugin_api import read_loan_inventory
from tests.test_p1_3252_atomic_scoped_loan_inventory import fixture


def test_paris_typed_read_ignores_v1_loans_and_preserves_stale_side_channel():
    session, a, _b = fixture()
    stale = [{"upstream": "SYRACUSE", "code": "old_unrelated_failure"}]
    session.last_loan_errors = stale
    session.loans = lambda _account: pytest.fail("legacy reader invoked")
    try:
        result = read_loan_inventory(session, a)
        assert result.status == "partial"
        assert result.reason_code == "branch_unavailable"
        assert [(b.upstream, b.completeness) for b in result.branches] == [
            ("dilicom", "partial"), ("syracuse", "unknown"),
        ]
        assert len(result.loans) == 1
        assert result.loans[0].source_account_id == a.unique_id
        assert result.loans[0].identity_id == a.identity_id
        assert session.last_loan_errors is stale
        assert session.last_loan_errors == stale
        assert session.client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
        assert not result.branch_claims_complete("syracuse", "physical")
    finally:
        session.close()


def test_paris_typed_second_borrower_does_not_rewrite_first_legacy_errors():
    session, a, b = fixture()
    try:
        assert len(session.loans(a)) == 1
        prior = session.last_loan_errors
        assert len(prior) == 1 and prior[0]["upstream"] == "DILICOM"
        report = session.loan_inventory_snapshot(b)
        assert report.status == "success"
        assert len(report.loans) == 2
        assert all(c.completeness == "unknown" for c in report.branches)
        assert not report.branch_claims_complete("dilicom", "digital")
        assert session.last_loan_errors is prior
        assert session.client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
            ("DILICOM", "reader-B"), ("SYRACUSE", "reader-B"),
        ]
    finally:
        session.close()


def test_failed_paris_typed_read_never_retries_v1_or_proves_absence():
    session, a, _b = fixture()
    attempts = []
    def fail_both(upstream, account_id):
        attempts.append((upstream, account_id))
        raise RuntimeError("synthetic upstream failure")
    session.client.loans = fail_both
    session.loans = lambda _account: pytest.fail("no second v1 GET")
    session.last_loan_errors = [{"upstream": "UNRELATED", "code": "old"}]
    try:
        report = read_loan_inventory(session, a)
        assert report.status == "failed"
        assert report.reason_code == "read_failed"
        assert report.loans == () and report.branches == ()
        assert attempts == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
        assert session.last_loan_errors == [
            {"upstream": "UNRELATED", "code": "old"},
        ]
    finally:
        session.close()
