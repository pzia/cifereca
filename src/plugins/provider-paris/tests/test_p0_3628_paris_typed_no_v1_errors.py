"""#3628: typed Paris loan producer does not depend on the v1 error channel.

Real provider session and private reader with synthetic DILICOM/SYRACUSE
responses. No network, loan mutation, token GET, ADEPT or Core writes.
"""
from dataclasses import replace

import pytest

from cifereca.plugin_api import LoanInventorySnapshot, read_loan_inventory
from tests.test_p1_3252_atomic_scoped_loan_inventory import fixture
from tests.test_p1_3252_rights_observation_snapshot import (
    fixture as scoped_fixture, upstream,
)


class Poison:
    def __iter__(self):
        pytest.fail("typed inventory read mutable v1 errors")

    def __bool__(self):
        pytest.fail("typed inventory inferred completeness from v1 errors")

    def __len__(self):
        pytest.fail("typed inventory read mutable v1 errors")


def test_typed_ignores_poisoned_legacy_facade_and_preserves_error_object():
    session, a, _ = fixture()
    poison = Poison()
    session.last_loan_errors = poison
    session.loans = lambda *_: pytest.fail("typed inventory called v1 loans")
    try:
        report = read_loan_inventory(session, replace(a))
        assert type(report) is LoanInventorySnapshot
        assert report.status == "partial"
        assert report.reason_code == "branch_unavailable"
        assert [(x.upstream, x.medium, x.completeness)
                for x in report.branches] == [
            ("dilicom", "digital", "partial"),
            ("syracuse", "physical", "unknown"),
        ]
        assert [x.source_account_id for x in report.loans] == ["reader-A"]
        assert session.last_loan_errors is poison
        assert session.client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        session.close()


def test_only_v1_reader_publishes_legacy_errors_for_existing_consumers():
    session, a, _ = fixture()
    try:
        typed = session.loan_inventory_snapshot(a)
        assert typed.status == "partial"
        assert session.last_loan_errors == []  # typed is read-only for v1
        legacy = session.loans(a)
        assert len(legacy) == len(typed.loans) == 1
        assert session.last_loan_errors == [{
            "upstream": "DILICOM", "code": "paris_loans_upstream_failed",
        }]
        # A second typed read must not erase this compatibility-only state.
        again = session.loan_inventory_snapshot(a)
        assert again.status == "partial"
        assert session.last_loan_errors == [{
            "upstream": "DILICOM", "code": "paris_loans_upstream_failed",
        }]
        assert len(session.client.calls) == 6
    finally:
        session.close()


@pytest.mark.parametrize("rights", [
    (), (upstream("loan-A", "edition-A"),),
    (upstream("loan-A", "edition-A"), upstream("loan-B", "edition-B")),
])
def test_scoped_0_1_n_typed_positive_only_without_exhaustive_negative(rights):
    session, account, client = scoped_fixture(digital=rights)
    try:
        result = session.loan_inventory_snapshot(replace(account))
        assert type(result) is LoanInventorySnapshot
        assert result.scope.identity_id == account.identity_id
        assert result.scope.credential_id == account.credential_id
        assert result.status == "success"
        assert len(result.loans) == len(rights)
        assert all(x.source_account_id == account.unique_id
                   and x.identity_id == account.identity_id
                   for x in result.loans)
        assert result.branch_claims_complete("dilicom", "digital") is False
        assert result.branch_claims_complete("syracuse", "physical") is False
        assert session.last_loan_errors == []
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        session.close()
