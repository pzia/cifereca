"""#3252: Paris DILICOM real upstream loan IDs vs descriptive fallback IDs.

Synthetic in-memory service only. No login/network, PNB/ACSM, disk or Core write.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import Account, ProviderConfigurationError, ProviderCapabilityError
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


class CoreOwnerBroker:
    def __init__(self, identities=("identity-A",)):
        self.identities = identities
        self.calls = 0

    def accounts(self):
        self.calls += 1
        return [
            NS(provider_id="paris", account_id="reader-A", identity_id=identity)
            for identity in self.identities
        ]


def fixture(*, digital=(), physical=()):
    broker = CoreOwnerBroker()
    session = ParisProviderSession(
        credential_id="card-A", label="Paris",
        credentials={}, options={}, config={}, diagnostics=Diagnostics(),
        identity_broker=broker,
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
        # New broker-backed rights preflight must refuse before the legacy
        # inventory reader (which retains its own scope-mismatch code).
        with pytest.raises(ProviderCapabilityError, match="paris_rights_owner_unverified"):
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


@pytest.mark.parametrize("identities", [
    (), ("identity-B",), ("identity-A", "identity-B"),
    ("identity-A", "identity-A"),
])
def test_paris_unverified_core_owner_never_calls_private_loan_branches(identities):
    session, account, client = fixture(digital=(upstream("loan-1", "edition-1"),))
    session.identity_broker.identities = identities
    try:
        with pytest.raises(ProviderCapabilityError, match="paris_rights_owner_unverified"):
            session.rights_observation_snapshot(account)
        assert client.calls == []
    finally:
        session.close()


def test_paris_missing_core_broker_refuses_only_new_rights_api():
    session, account, client = fixture(digital=(upstream("loan-1", "edition-1"),))
    session.identity_broker = None
    try:
        with pytest.raises(ProviderCapabilityError, match="paris_rights_owner_unverified"):
            session.rights_observation_snapshot(account)
        assert client.calls == []
        old = session.loan_inventory_snapshot(account)
        assert len(old.loans) == 1
        assert client.calls == [("DILICOM", "reader-A"), ("SYRACUSE", "reader-A")]
    finally:
        session.close()


@pytest.mark.parametrize("race", [
    "owner_reassigned", "same_owner_new_broker", "duplicate_owner",
])
def test_paris_core_owner_rebind_mid_dilicom_refuses_final_rights(race):
    session, account, client = fixture(digital=(upstream("loan-1", "edition-1"),))
    broker = session.identity_broker
    original = client.loans

    def changed_loans(branch, owner_id):
        rows = original(branch, owner_id)
        if branch == "DILICOM":
            if race == "owner_reassigned":
                broker.identities = ("identity-B",)
            elif race == "same_owner_new_broker":
                session.identity_broker = CoreOwnerBroker()
            else:
                broker.identities = ("identity-A", "identity-A")
        return rows

    client.loans = changed_loans
    try:
        with pytest.raises(ProviderConfigurationError, match="account_scope_mismatch"):
            session.rights_observation_snapshot(account)
        assert client.calls == [("DILICOM", "reader-A"), ("SYRACUSE", "reader-A")]
    finally:
        session.close()


def test_paris_identity_changed_before_observation_refuses_both_private_gets():
    session, account, client = fixture(digital=(upstream("loan-1", "edition-1"),))
    account.identity_id = "identity-B"
    try:
        with pytest.raises(ProviderCapabilityError, match="paris_rights_owner_unverified"):
            session.rights_observation_snapshot(account)
        assert client.calls == []
    finally:
        session.close()


def test_paris_plugin_lifecycle_injects_the_public_core_identity_broker():
    from cifereca_paris import ParisProviderPlugin

    broker = CoreOwnerBroker()
    plugin = ParisProviderPlugin()
    plugin.activate(NS(broker=lambda key: broker if key == "identities" else None))
    session = plugin.create_session(
        credential_id="card-A", label="Paris", credentials={},
        options={}, config={}, storage=None, diagnostics=Diagnostics(),
    )
    try:
        assert session.identity_broker is broker
        plugin.deactivate()
        assert plugin._identity_broker is None
        assert session.identity_broker is broker
    finally:
        session.close()


@pytest.mark.parametrize("duplicate_records", [
    # Identical repeated page card and contradictory edition for one loan ID:
    # neither case is proof of two independent rights.
    (upstream("loan-repeated", "edition-A"),
     upstream("loan-repeated", "edition-A")),
    (upstream("loan-repeated", "edition-A"),
     upstream("loan-repeated", "edition-B")),
])
def test_paris_duplicate_loan_id_quarantines_both_copies_not_other_loans(
    duplicate_records,
):
    session, account, client = fixture(digital=(
        upstream("loan-unique-1", "edition-1"),
        duplicate_records[0],
        upstream("loan-unique-2", "edition-2"),
        duplicate_records[1],
        duplicate_records[0],  # repeated a third time remains quarantined
    ))
    try:
        snapshot = session.rights_observation_snapshot(account)
        assert [(right.right_id, right.edition_id) for right in snapshot.rights] == [
            ("loan-unique-1", "edition-1"),
            ("loan-unique-2", "edition-2"),
        ]
        assert snapshot.exact("edition-A", "loan-repeated", "loan") is None
        assert snapshot.exact("edition-B", "loan-repeated", "loan") is None
        assert snapshot.status == snapshot.coverage == "partial"
        assert snapshot.pagination_complete is False
        assert not snapshot.authorizes_negative_reconciliation
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        session.close()


def test_paris_two_distinct_loans_for_one_edition_are_not_duplicate_rights():
    session, account, client = fixture(digital=(
        upstream("loan-1", "same-edition"),
        upstream("loan-2", "same-edition"),
    ))
    try:
        snapshot = session.rights_observation_snapshot(account)
        assert [(right.right_id, right.edition_id) for right in snapshot.rights] == [
            ("loan-1", "same-edition"), ("loan-2", "same-edition"),
        ]
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        session.close()


def test_paris_empty_primary_borrower_matches_credential_scoped_core_account_ref():
    """The Core AccountRef key is not the provider's empty upstream account."""
    session, account, client = fixture(digital=(
        upstream("primary-loan", "primary-edition"),
    ))
    account.unique_id = ""  # Upstream Paris primary borrower convention
    session.identity_broker.accounts = lambda: [
        NS(provider_id="paris", account_id="paris:card-A:primary",
           identity_id="identity-A"),
        # An empty primary from another card must NOT be accepted for card-A.
        NS(provider_id="paris", account_id="paris:other-card:primary",
           identity_id="identity-B"),
    ]
    try:
        observed = session.rights_observation_snapshot(account)
        assert observed.scope.identity_id == "identity-A"
        assert observed.scope.account_id == ""
        assert observed.scope.credential_id == "card-A"
        assert [(r.right_id, r.edition_id) for r in observed.rights] == [
            ("primary-loan", "primary-edition"),
        ]
        assert observed.status == observed.coverage == "partial"
        assert not observed.authorizes_negative_reconciliation
        assert client.calls == [("DILICOM", ""), ("SYRACUSE", "")]
    finally:
        session.close()


def test_paris_empty_primary_refuses_foreign_credential_owner_before_network():
    session, account, client = fixture(digital=(
        upstream("primary-loan", "primary-edition"),
    ))
    account.unique_id = ""
    session.identity_broker.accounts = lambda: [
        NS(provider_id="paris", account_id="paris:other-card:primary",
           identity_id="identity-A"),
    ]
    try:
        with pytest.raises(ProviderCapabilityError, match="paris_rights_owner_unverified"):
            session.rights_observation_snapshot(account)
        assert client.calls == []
    finally:
        session.close()

@pytest.mark.parametrize("failed_branch", ["DILICOM", "SYRACUSE"])
def test_paris_branch_failure_preserves_exact_rights_observation_status(failed_branch):
    """The DILICOM rights producer must not treat a failed digital GET as
    a successful empty page merely because the physical sibling answered.
    """
    session, account, client = fixture(
        digital=(upstream("digital-loan", "edition-digital"),),
        physical=(upstream("physical-loan", "edition-physical"),),
    )
    original = client.loans

    def fail_one_branch(branch, account_id):
        if branch == failed_branch:
            client.calls.append((branch, account_id))
            raise RuntimeError("SYNTHETIC_PROVIDER_BRANCH_OUTAGE")
        return original(branch, account_id)

    client.loans = fail_one_branch
    try:
        report = session.rights_observation_snapshot(account)
        if failed_branch == "DILICOM":
            assert report.status == "failed"
            assert report.coverage == "unknown"
            assert report.rights == ()
        else:
            assert report.status == "partial"
            assert report.coverage == "partial"
            assert [(x.right_id, x.edition_id) for x in report.rights] == [
                ("digital-loan", "edition-digital"),
            ]
        assert report.branch == "dilicom"
        assert report.pagination_complete is False
        assert report.authorizes_negative_reconciliation is False
        assert report.authorizes_mutation is False
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        session.close()
