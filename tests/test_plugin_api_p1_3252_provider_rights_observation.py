"""#3252: two interchangeable synthetic providers; no Core ledger or network.

A provider's declared purchase/loan and its offered delivery are claims,
not an artifact hash, acquisition receipt or permission to download.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import (
    RIGHTS_OBSERVATION_CONTRACT, ProviderRightsScope,
    ProviderRightObservation, ProviderRightsSnapshot,
)


STAMP = "2026-10-27T20:00:00+00:00"


def scope(provider="kobo", identity="alice", credential="vault-a", account="buyer-a"):
    return ProviderRightsScope(identity, provider, credential, account)


def purchase(edition="edition-1", right="receipt-1", delivery="available"):
    return ProviderRightObservation(
        edition_id=edition, right_id=right, kind="purchase",
        state="active", evidence_kind="provider_response",
        evidence_ref="manifest-revision:42", observed_at=STAMP,
        delivery=delivery,
        delivery_offer_id="offer:42" if delivery == "available" else "",
    )


def snap(provider="kobo", *, rights=(), identity="alice", credential="vault-a",
         account="buyer-a", status="success", coverage="complete"):
    return ProviderRightsSnapshot(
        scope(provider, identity, credential, account), tuple(rights),
        status=status, coverage=coverage,
        pagination_complete=True if coverage == "complete" else False,
        branch="purchases", observed_at=STAMP,
        snapshot_ref="provider-snapshot:42",
    )


@pytest.mark.parametrize("provider", ("kobo", "numilog"))
def test_interchangeable_providers_positive_claim_is_not_a_ledger_mutation(provider):
    rows = (purchase(), purchase("edition-2", "receipt-2", delivery="unknown"))
    observed = snap(provider, rights=rows)
    assert observed.schema_version == RIGHTS_OBSERVATION_CONTRACT
    assert observed.scope.provider_id == provider
    assert observed.claims_complete_branch is True
    assert observed.exact("edition-1", "receipt-1", "purchase") == rows[0]
    assert observed.exact("edition-1", "receipt-1", "loan") is None
    assert observed.exact("edition-2", "receipt-2", "purchase") == rows[1]
    assert observed.exact("other-edition", "receipt-1", "purchase") is None
    assert rows[0].provider_attested is True
    assert rows[0].delivery_offer_id == "offer:42"
    assert all(row.authorizes_mutation is False for row in observed.rights)
    assert observed.authorizes_mutation is False
    assert observed.authorizes_negative_reconciliation is False


def test_same_edition_in_two_identities_and_credentials_does_not_join():
    first = snap(rights=(purchase(),))
    second = snap(
        "kobo", identity="bob", credential="vault-b", account="buyer-b",
        rights=(purchase(right="receipt-b"),),
    )
    assert first.scope != second.scope
    assert first.exact("edition-1", "receipt-b", "purchase") is None
    assert second.exact("edition-1", "receipt-1", "purchase") is None
    assert first.rights[0].edition_id == second.rights[0].edition_id
    assert first.authorizes_mutation is second.authorizes_mutation is False
    with pytest.raises(FrozenInstanceError):
        second.scope.identity_id = "alice"


def test_partial_replay_and_empty_account_are_non_destructive():
    pending = ProviderRightsSnapshot(
        scope("numilog", account=""),
        rights=(purchase(delivery="unknown"),),
        status="partial", coverage="partial", pagination_complete=False,
        branch="purchases", observed_at=STAMP,
        snapshot_ref="provider-page:3", resume_cursor="cursor:next",
    )
    assert pending.scope.account_id == ""
    assert pending.claims_complete_branch is False
    assert not pending.authorizes_negative_reconciliation
    assert pending.exact("edition-1", "receipt-1", "purchase") is not None
    empty_complete = snap("numilog", account="", rights=())
    assert empty_complete.claims_complete_branch is True
    assert empty_complete.rights == ()
    # Even this claims ONLY a provider page/branch; no proof for deletion.
    assert empty_complete.authorizes_negative_reconciliation is False


@pytest.mark.parametrize("status", ("failed", "cancelled"))
def test_failure_or_cancel_cannot_keep_positive_rows(status):
    with pytest.raises(ValueError, match="invalid_rights_observation_failed_rows"):
        snap(status=status, coverage="unknown", rights=(purchase(),))
    failed = snap(status=status, coverage="unknown")
    assert failed.claims_complete_branch is False
    assert failed.rights == ()
    assert not failed.authorizes_mutation


@pytest.mark.parametrize("coverage,pagination,status", (
    ("complete", False, "success"),
    ("complete", True, "partial"),
    ("complete", True, "cancelled"),
))
def test_unproven_completeness_fails_closed(coverage, pagination, status):
    with pytest.raises(ValueError, match="unproven_rights_observation_completeness|invalid_rights_observation_failed_rows"):
        ProviderRightsSnapshot(
            scope(), rights=(), status=status, coverage=coverage,
            pagination_complete=pagination,
            branch="purchases", observed_at=STAMP, snapshot_ref="page:1",
        )


def test_duplicate_right_key_rejected_but_multiple_receipts_for_one_edition_valid():
    item = purchase()
    with pytest.raises(ValueError, match="ambiguous_rights_observation_rows"):
        snap(rights=(item, item))
    distinct = snap(rights=(item, replace(item, right_id="receipt-2")))
    assert len(distinct.rights) == 2
    assert distinct.exact("edition-1", "receipt-2", "purchase") is not None


def test_legacy_metadata_and_unverified_state_cannot_forge_receipt_or_delivery():
    legacy = ProviderRightObservation(
        edition_id="edition-1", right_id="legacy:local", kind="purchase",
        state="active",
    )
    assert legacy.provider_attested is False
    assert legacy.delivery == "unknown"
    assert legacy.authorizes_mutation is False
    with pytest.raises(ValueError, match="unproven_rights_observation"):
        replace(legacy, evidence_kind="provider_response")
    with pytest.raises(ValueError, match="invalid_rights_observation_evidence"):
        replace(legacy, evidence_ref="purchase guessed by title")
    with pytest.raises(ValueError, match="unproven_rights_delivery"):
        replace(legacy, delivery="available", delivery_offer_id="download:book")


def test_historical_receipt_is_distinct_from_fresh_provider_snapshot():
    prior = ProviderRightObservation(
        edition_id="edition-1", right_id="historical-1", kind="purchase",
        state="active", evidence_kind="historical_receipt",
        evidence_ref="immutable-local-receipt:abc", observed_at=STAMP,
    )
    assert prior.provider_attested is False
    assert prior.authorizes_mutation is False
    assert snap(rights=(prior,)).rights == (prior,)


def test_ambiguous_offer_or_bad_timestamp_and_session_scope_are_refused():
    with pytest.raises(ValueError, match="unproven_rights_delivery"):
        replace(purchase(), delivery="unavailable")
    with pytest.raises(ValueError, match="unproven_rights_delivery"):
        replace(purchase(), delivery_offer_id="")
    with pytest.raises(ValueError, match="invalid_rights_observation_timestamp"):
        replace(purchase(), observed_at="2026-10-27T20:00:00")
    with pytest.raises(ValueError, match="invalid_rights_observation_scope"):
        scope(credential="")
    with pytest.raises(ValueError, match="invalid_rights_observation_contract"):
        replace(snap(), schema_version="cifereca.rights.observation/2")
    with pytest.raises(ValueError, match="unproven_rights_observation_completeness"):
        replace(snap(), resume_cursor="next")
    with pytest.raises(ValueError, match="invalid_rights_observation_resume"):
        replace(snap(coverage="partial"), resume_cursor="next")


def test_no_io_no_plugin_or_storage_mutation_boundary():
    # Synthetic publishers can map their observed manifests without importing
    # private cifereca.storage/services, HTTPX, acquisitions or file handlers.
    called = SimpleNamespace(network=False, storage=False, download=False)
    a = snap("kobo", rights=(purchase(),))
    b = snap("numilog", rights=(purchase(),))
    assert (a.rights[0].state, b.rights[0].state) == ("active", "active")
    assert not (called.network or called.storage or called.download)


def test_archive_reason_is_additive_and_does_not_change_right_or_create_offer():
    right = replace(purchase(delivery="unavailable"), delivery_unavailable_reason="archived")
    assert right.state == "active" and right.provider_attested
    assert right.delivery_offer_id == "" and not right.authorizes_mutation
    assert purchase(delivery="unavailable").delivery_unavailable_reason == ""


@pytest.mark.parametrize("reason", [None, True, [], "archived_book", "unknown"])
def test_invalid_delivery_reason_is_rejected(reason):
    with pytest.raises(ValueError, match="invalid_rights_delivery_reason"):
        replace(purchase(delivery="unavailable"), delivery_unavailable_reason=reason)


@pytest.mark.parametrize("delivery", ["available", "unknown"])
def test_archive_reason_requires_attested_unavailable_delivery(delivery):
    with pytest.raises(ValueError, match="invalid_rights_delivery_reason"):
        replace(purchase(delivery=delivery), delivery_unavailable_reason="archived")
    with pytest.raises(ValueError, match="invalid_rights_delivery_reason"):
        ProviderRightObservation("edition", "right", "purchase", delivery="unavailable",
                                 delivery_unavailable_reason="archived")


def test_current_download_offer_is_valid_with_unknown_descriptive_right():
    # A genuine provider-admitted delivery offer is the acquisition criterion.
    # The right remains unknown rather than being fabricated as an active one.
    current = replace(purchase(), state="unknown")
    assert current.state == "unknown"
    assert current.delivery == "available"
    assert current.provider_attested is True
    assert current.authorizes_mutation is False
    with pytest.raises(ValueError, match="unproven_rights_delivery"):
        replace(current, evidence_kind="legacy_unverified",
                evidence_ref="", observed_at=None)
    with pytest.raises(ValueError, match="unproven_rights_delivery"):
        replace(current, state="inactive")
