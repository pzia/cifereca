"""P0 #5073: remote purchase UI requires Core-owned, exact rights ledger."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.search_owned_evidence import (
    SearchOwnedEvidence, snapshot_search_owned_evidence,
    search_result_has_verified_purchase,
)
from cifereca.gui.query_model import result_row
from tests.test_search_results_view import _book


IDENTITY = "reader-A"
PROVIDER = "fictional-provider"
CREDENTIAL = "credential-A"
ACCOUNT = "account-A"
EDITION = "edition-A"
REVISION = "revision-A"


def _item():
    return replace(
        _book(PROVIDER, record=EDITION),
        source_credential=CREDENTIAL, source_account_id=ACCOUNT,
        revision_id=REVISION, owned=True,
    )


def _entitlement(**changes):
    value = {
        "identity_id": IDENTITY,
        "provider_id": PROVIDER,
        "credential_id": CREDENTIAL,
        "account_id": ACCOUNT,
        "item_id": EDITION,
        "revision_id": REVISION,
        "entitlement_type": "owned",
        "state": "active",
    }
    value.update(changes)
    return value


def _service(rows, *, identity=IDENTITY):
    storage = NS(entitlements=Mock(return_value=rows))
    return NS(
        current_identity=NS(identity_id=identity), storage=storage,
        search=Mock(side_effect=AssertionError("provider GET forbidden")),
    )


def test_untrusted_flags_never_mark_owned_without_independent_ledger():
    item = _item()
    assert result_row(item)["state"] == "remote_catalog"
    assert result_row(item)["owned"] is False  # no unverified flag leak via dict
    assert result_row(replace(item, source_provider="numilog",
                              scenario="NUMILOG_LIBRARY", source_collection="library"))["state"] == "remote_catalog"
    assert result_row(replace(item, source_provider="kobo"))["state"] == "remote_catalog"


def test_exact_canonical_ownership_works_for_multiple_unrelated_providers():
    for provider in (PROVIDER, "numilog", "kobo"):
        item = replace(_item(), source_provider=provider)
        service = _service([_entitlement(provider_id=provider)])
        proof = snapshot_search_owned_evidence(service)
        assert type(proof) is SearchOwnedEvidence
        assert result_row(item, purchase_evidence=proof,
                          identity_id=IDENTITY)["state"] == "remote_owned"
        assert result_row(item, purchase_evidence=proof,
                          identity_id=IDENTITY)["owned"] is True
        service.storage.entitlements.assert_called_once_with(state="active")
        service.search.assert_not_called()


@pytest.mark.parametrize("change", [
    {"source_provider": "foreign-provider"},
    {"source_credential": "foreign-credential"},
    {"source_credential": "__public__"},
    {"source_account_id": "foreign-account"},
    {"source_account_id": None},
    {"record_id": "foreign-edition"},
    {"revision_id": "foreign-revision"},
    {"revision_id": None},
    {"owned": True, "source_collection": "public_catalog"},
    {"owned": True, "source_collection": "catalog"},
    {"owned": False},
    {"owned": None},
    {"owned": 1},
    {"metadata": {"search_provenance": "known_catalog_stale"}},
])
def test_foreign_or_stale_source_cannot_reuse_private_evidence(change):
    service = _service([_entitlement()])
    proof = snapshot_search_owned_evidence(service)
    item = replace(_item(), **change)
    assert result_row(item, purchase_evidence=proof,
                      identity_id=IDENTITY)["state"] == "remote_catalog"


def test_cross_identity_or_changed_ledger_revokes_the_verdict():
    service = _service([_entitlement()])
    proof = snapshot_search_owned_evidence(service)
    assert proof is not None
    assert result_row(_item(), purchase_evidence=proof,
                      identity_id="reader-B")["state"] == "remote_catalog"
    assert snapshot_search_owned_evidence(
        _service([_entitlement(identity_id="reader-B")])
    ).rights == frozenset()
    service.current_identity = NS(identity_id="reader-B")
    assert result_row(_item(), purchase_evidence=proof,
                      identity_id=service.current_identity.identity_id)["state"] == "remote_catalog"


def test_account_or_source_rebinding_revokes_old_proof_even_same_identity():
    service = _service([_entitlement()])
    service._account_context_epoch = 17
    service._source_context_epoch = 29
    proof = snapshot_search_owned_evidence(service)
    assert proof is not None
    assert result_row(
        _item(), purchase_evidence=proof, identity_id=IDENTITY,
        account_context_epoch=17, source_context_epoch=29,
    )["state"] == "remote_owned"
    for account_epoch, source_epoch in ((18, 29), (17, 30), (None, None)):
        assert result_row(
            _item(), purchase_evidence=proof, identity_id=IDENTITY,
            account_context_epoch=account_epoch, source_context_epoch=source_epoch,
        )["state"] == "remote_catalog"


def test_account_epoch_changed_during_ledger_read_refuses_snapshot():
    service = _service([_entitlement()])
    service._account_context_epoch = 5
    service._source_context_epoch = 7
    def rotate(*, state):
        service._source_context_epoch += 1
        return [_entitlement()]
    service.storage.entitlements.side_effect = rotate
    assert snapshot_search_owned_evidence(service) is None


def test_pending_archived_and_loan_entitlements_are_not_purchase_proofs():
    rows = [
        _entitlement(entitlement_type="loan"),
        _entitlement(state="archived"),
        _entitlement(entitlement_type="purchase_request"),
    ]
    proof = snapshot_search_owned_evidence(_service(rows))
    assert result_row(_item(), purchase_evidence=proof,
                      identity_id=IDENTITY)["state"] == "remote_catalog"


def test_identity_swap_during_sqlite_projection_fails_closed():
    service = _service([_entitlement()])
    def swap(*, state):
        service.current_identity = NS(identity_id="reader-B")
        return [_entitlement()]
    service.storage.entitlements.side_effect = swap
    assert snapshot_search_owned_evidence(service) is None


@pytest.mark.parametrize("rows", [None, (), "bad", [None], [object()], [1]])
def test_malformed_entitlements_never_publish_partial_ownership(rows):
    assert snapshot_search_owned_evidence(_service(rows)) is None


def test_raw_provider_metadata_cannot_supply_rights_or_source_coordinates():
    item = replace(_item(), metadata={
        "owned": True, "source_account_id": ACCOUNT,
        "entitlement_type": "owned", "identity_id": IDENTITY,
        "right_id": EDITION, "provider_verified": True,
    }, source_account_id=None)
    proof = snapshot_search_owned_evidence(_service([_entitlement()]))
    assert result_row(item, purchase_evidence=proof,
                      identity_id=IDENTITY)["state"] == "remote_catalog"
