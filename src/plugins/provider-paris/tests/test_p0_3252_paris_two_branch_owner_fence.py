"""#3252: Paris must never read the second private branch after Core owner rebind.

Synthetic DILICOM/SYRACUSE ListLoans responses only. No provider network,
file GET, PNB POST, SQLite or negative-rights reconciliation.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderConfigurationError
from tests.test_p1_3252_rights_observation_snapshot import fixture, upstream, CoreOwnerBroker


@pytest.mark.parametrize("change", [
    "owner_B", "owner_duplicate", "broker_ABA", "broker_removed",
])
@pytest.mark.parametrize("entrypoint", ["legacy", "typed_rights"])
def test_second_paris_branch_get_blocked_after_core_owner_changes(change, entrypoint):
    session, account, client = fixture(digital=(
        upstream("loan-a", "edition-a"),
    ), physical=(upstream("loan-b", "edition-b"),))
    original = client.loans
    def first(upstream_name, account_id):
        rows = original(upstream_name, account_id)
        if upstream_name == "DILICOM":
            if change == "owner_B":
                session.identity_broker.identities = ("identity-B",)
            elif change == "owner_duplicate":
                session.identity_broker.identities = ("identity-A", "identity-A")
            elif change == "broker_ABA":
                session.identity_broker = CoreOwnerBroker()
            else:
                session.identity_broker = None
        return rows
    client.loans = first
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            if entrypoint == "legacy":
                session.loans(account)
            else:
                session.rights_observation_snapshot(account)
        assert client.calls == [("DILICOM", "reader-A")]
    finally:
        session.close()


@pytest.mark.parametrize("entrypoint", ["legacy", "typed_rights"])
def test_addition_of_foreign_identity_midread_does_not_block_same_borrower(entrypoint):
    session, account, client = fixture(digital=(
        upstream("loan-a", "edition-a"),
    ), physical=(upstream("loan-b", "edition-b"),))
    original = client.loans

    def first(upstream_name, account_id):
        rows = original(upstream_name, account_id)
        if upstream_name == "DILICOM":
            # Same account ID under a *different* Core identity does not
            # change the originally pinned owner in identity-A.
            session.identity_broker.identities = ("identity-A", "identity-B")
        return rows

    client.loans = first
    try:
        if entrypoint == "legacy":
            assert len(session.loans(account)) == 2
        else:
            report = session.rights_observation_snapshot(account)
            assert [right.right_id for right in report.rights] == ["loan-a"]
            assert report.scope.identity_id == "identity-A"
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        session.close()


@pytest.mark.parametrize("owners", [
    ("identity-A",), ("identity-A", "identity-B"), (),
])
def test_stable_legacy_paris_branch_reads_keep_existing_behavior(owners):
    s, account, client = fixture(digital=(upstream("loan-a", "edition-a"),))
    s.identity_broker.identities = owners
    try:
        assert [loan.id for loan in s.loans(replace(account))] == ["loan-a"]
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        s.close()


def test_brokerless_legacy_paris_reads_both_branches_but_no_rights_authority():
    s, account, client = fixture(digital=(upstream("loan-a", "edition-a"),))
    s.identity_broker = None
    try:
        assert len(s.loans(account)) == 1
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        s.close()


def test_other_v2_credential_does_not_rebind_same_paris_reader():
    s, account, client = fixture(digital=(upstream("loan-a", "edition-a"),))
    s.identity_broker.accounts = lambda identity_id=None: [
        NS(provider_id="paris", account_id="reader-A",
           identity_id="identity-A", credential_id="card-A", scope_version=2),
        NS(provider_id="paris", account_id="reader-A",
           identity_id="identity-B", credential_id="card-B", scope_version=2),
    ]
    try:
        report = s.rights_observation_snapshot(account)
        assert [right.right_id for right in report.rights] == ["loan-a"]
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        s.close()
