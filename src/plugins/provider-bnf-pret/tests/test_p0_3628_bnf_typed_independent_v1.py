"""#3628 BnF: typed inventory uses a single locked producer, never v1 facade.

Real BnfProviderSession with synthetic HTML activity; no account, token GET,
ADEPT call, return or mutation of Core rights.
"""
import pytest

from cifereca.plugin_api import read_loan_inventory
from tests.test_p1_3252_snapshot_scope_mutation import fixture
from tests.test_p1_3252_rights_observation_snapshot import (
    activity, fixture as rights_fixture,
)


def test_typed_bnf_ignores_poisoned_v1_facade_without_second_get():
    session, account, client = fixture()
    try:
        session.loans = lambda _account: pytest.fail("v1 reader must not run")
        snapshot = read_loan_inventory(session, account)
        assert snapshot.status == "partial"
        assert len(snapshot.loans) == 1
        assert snapshot.reason_code == "inventory_not_exhaustive"
        assert snapshot.scope.identity_id == account.identity_id
        assert not snapshot.branch_claims_complete("bnf_html", "digital")
        assert client.calls == ["login", "activity"]
    finally:
        session.close()


def test_existing_v1_bnf_clients_remain_positive_and_non_exhaustive():
    session, account, client = fixture()
    try:
        positive = session.loans(account)
        assert len(positive) == 1
        assert positive[0].identity_id == account.identity_id
        assert positive[0].raw["inventory_exhaustive"] is False
        assert client.calls == ["login", "activity"]
    finally:
        session.close()


def test_empty_bnf_html_cannot_claim_negative_or_pagination_coverage():
    session, account, client = rights_fixture(activity())
    try:
        session.loans = lambda _account: pytest.fail("no legacy GET")
        report = read_loan_inventory(session, account)
        assert report.loans == ()
        assert report.status == "partial"
        assert len(report.branches) == 1
        assert report.branches[0].completeness == "partial"
        assert report.branches[0].pagination_complete is not True
        assert not report.branch_claims_complete("bnf_html", "digital")
        assert client.calls == ["login", "activity"]
    finally:
        session.close()
