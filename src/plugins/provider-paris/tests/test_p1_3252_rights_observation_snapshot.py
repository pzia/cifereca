"""#3252: Paris DILICOM real upstream loan IDs vs descriptive fallback IDs.

Synthetic in-memory service only. No login/network, PNB/ACSM, disk or Core write.
"""
from dataclasses import replace

import pytest

from cifereca.plugin_api import Account, ProviderConfigurationError
from cifereca_paris import ParisProviderSession
from cifereca_paris_normalize import normalize_loan


class Diagnostics:
    def add(self, *args, **kwargs):
        pass


class Client:
    def __init__(self, digital=(), physical=()):
        self.digital, self.physical = list(digital), list(physical)
        self.calls = []

    def loans(self, upstream, account_id):
        self.calls.append((upstream, account_id))
        return [
            normalize_loan(dict(raw), upstream)
            for raw in (self.digital if upstream == "DILICOM" else self.physical)
        ]

    def close(self):
        pass


def fixture(*, digital=(), physical=()):
    session = ParisProviderSession(
        credential_id="card-A", label="Paris",
        credentials={}, options={}, config={}, diagnostics=Diagnostics(),
    )
    session.client.close()
    client = Client(digital=digital, physical=physical)
    session.client = client
    session._enrich_digital_loan_metadata = lambda loan: None
    account = Account(
        display_name="Reader", unique_id="reader-A",
        provider_id="paris", credential_id="card-A",
        identity_id="identity-A",
    )
    session.connected = True
    session._accounts = [account]
    return session, account, client


def upstream(loan_id, record_id):
    return {"Id": loan_id, "RecordId": record_id, "Title": "Synthetic"}


@pytest.mark.parametrize("digital", [
    (), (upstream("loan-1", "edition-1"),),
    (upstream("loan-1", "edition-1"), upstream("loan-2", "edition-2")),
])
def test_paris_0_1_n_attested_digital_loans_are_positive_only(digital):
    session, account, client = fixture(
        digital=digital, physical=(upstream("physical-3", "edition-3"),),
    )
    try:
        result = session.rights_observation_snapshot(account)
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
        assert (result.scope.identity_id, result.scope.provider_id,
                result.scope.credential_id, result.scope.account_id) == (
                    "identity-A", "paris", "card-A", "reader-A",
                )
        assert result.branch == "dilicom"
        assert result.status == result.coverage == "partial"
        assert result.pagination_complete is False
        assert not result.claims_complete_branch
        assert not result.authorizes_negative_reconciliation
        assert not result.authorizes_mutation
        assert [(r.right_id, r.edition_id) for r in result.rights] == [
            (r["Id"], r["RecordId"]) for r in digital
        ]
        for right in result.rights:
            assert right.kind == "loan"
            assert right.state == "unknown"  # no guessed active/expired state
            assert right.provider_attested
            assert right.evidence_ref == "paris.dilicom.listloans"
            assert right.delivery == "unknown" and right.delivery_offer_id == ""
            assert not right.authorizes_mutation
    finally:
        session.close()


def test_paris_never_promotes_generated_title_or_uuid_fallback_as_right():
    session, account, client = fixture(digital=(
        {"Title": "Synthetic", "RecordId": "edition-title-only"},
        {"Id": True, "RecordId": "edition-invalid-bool", "Title": "Synthetic"},
        {"Id": "loan-no-edition", "Title": "Synthetic"},
        {"HoldingId": "holding-4", "RecordId": "edition-4", "Title": "Synthetic"},
    ))
    try:
        result = session.rights_observation_snapshot(account)
        assert [(r.right_id, r.edition_id) for r in result.rights] == [
            ("holding-4", "edition-4"),
        ]
        assert result.status == "partial"
        assert not result.authorizes_negative_reconciliation
        assert len(client.calls) == 2
    finally:
        session.close()


def test_paris_mutable_caller_scope_changed_during_projection_is_rejected():
    session, account, client = fixture(digital=(
        upstream("loan-1", "edition-1"),
    ))
    original = session._snapshot_for_account

    def rebind_after_validated_read(borrower):
        snapshot = original(borrower)
        account.identity_id = "identity-B"
        return snapshot

    session._snapshot_for_account = rebind_after_validated_read
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.rights_observation_snapshot(account)
        assert len(client.calls) == 2
    finally:
        session.close()


def test_paris_wrong_credential_refused_before_both_gets():
    session, account, client = fixture(digital=(
        upstream("loan-1", "edition-1"),
    ))
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.rights_observation_snapshot(
                replace(account, credential_id="other-card"),
            )
        assert client.calls == []
    finally:
        session.close()


@pytest.mark.parametrize("rebind", [
    "replace_same_values", "remove", "duplicate_same_values",
    "replace_other_identity",
])
def test_paris_live_borrower_replaced_after_inventory_blocks_rights_projection(rebind):
    """The final envelope must not use a retired borrower instance.

    Mutation happens AFTER the legacy typed reader has already checked its
    owner. A replacement with identical values is still a different session.
    """
    session, account, client = fixture(digital=(
        upstream("loan-1", "edition-1"),
    ))
    original = session._snapshot_for_account

    def rebind_after_inventory(borrower):
        inventory = original(borrower)
        if rebind == "remove":
            session._accounts = []
        elif rebind == "duplicate_same_values":
            session._accounts.append(replace(account))
        else:
            other = replace(account)
            if rebind == "replace_other_identity":
                other.identity_id = "identity-B"
            session._accounts = [other]
        return inventory

    session._snapshot_for_account = rebind_after_inventory
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.rights_observation_snapshot(account)
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        session.close()


def test_paris_copy_of_bound_borrower_remains_compatible_after_instance_pin():
    session, account, client = fixture(digital=(
        upstream("loan-1", "edition-1"),
    ))
    try:
        report = session.rights_observation_snapshot(replace(account))
        assert len(report.rights) == 1
        assert report.scope.account_id == "reader-A"
        assert report.scope.identity_id == "identity-A"
        assert len(client.calls) == 2
    finally:
        session.close()
