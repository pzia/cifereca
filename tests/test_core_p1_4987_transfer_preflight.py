"""#4987: placement/claim read-only preflight must not send loaned bytes."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.borrow_for_identity_transfer_preflight import (
    preflight_cross_profile_loan_transfer as inspect,
)
from cifereca.core.borrow_for_identity_progress import CrossProfileBorrowProgress
from tests.test_core_p1_4662_cross_profile_borrow_preview import witness


@pytest.fixture
def scope(monkeypatch):
    w = witness()
    reader = NS(identity_id="bob")
    storage = NS(placement_get=Mock(return_value={
        "identity_id": w.account.identity_id,
        "provider_id": w.account.provider_id,
        "source_item_id": w.notice.record_id,
        "state": "present", "artifact_id": "sha256:synthetic",
    }))
    service = NS(
        current_identity=reader, _account_context_epoch=3,
        _source_context_epoch=4, storage=storage,
        materialize_to_identity=Mock(side_effect=AssertionError("no copy")),
    )
    monkeypatch.setattr(
        "cifereca.core.borrow_for_identity_transfer_preflight."
        "observed_cross_profile_borrow_progress",
        lambda *args, **kw: CrossProfileBorrowProgress(
            "confirmed", "unavailable", "blocked",
            "lender_loan_confirmed_transfer_unverified",
        ),
    )
    return service, w


def checked(service, w, **overrides):
    return inspect(
        service, requesting_identity_id=overrides.get("reader", "bob"),
        borrowing_account=w.account, exact_notice=w.notice,
        lender_placement_id=overrides.get("placement", "placement-a"),
    )


def test_matching_local_lender_placement_admits_explicit_send(scope):
    service, w = scope
    result = checked(service, w)
    assert (result.loan, result.delivery, result.transfer) == (
        "confirmed", "present", "ready_for_send",
    )
    assert result.reason_code == "lender_placement_present_send_available"
    assert result.executable is True and result.retry_borrow is False
    service.storage.placement_get.assert_called_once_with("placement-a")
    service.materialize_to_identity.assert_not_called()


@pytest.mark.parametrize("change", [
    {"identity_id": "bob"}, {"provider_id": "another"},
    {"source_item_id": "another"}, {"state": "missing"},
    {"artifact_id": ""}, {"artifact_id": None},
])
def test_wrong_or_incomplete_placement_cannot_be_used(scope, change):
    service, w = scope
    row = service.storage.placement_get.return_value.copy()
    row.update(change)
    service.storage.placement_get.return_value = row
    outcome = checked(service, w)
    assert outcome.reason_code == "lender_delivery_unverified"
    assert outcome.transfer == "blocked" and outcome.executable is False
    service.materialize_to_identity.assert_not_called()


def test_no_confirmed_claim_cannot_even_query_placement(scope, monkeypatch):
    service, w = scope
    monkeypatch.setattr(
        "cifereca.core.borrow_for_identity_transfer_preflight."
        "observed_cross_profile_borrow_progress",
        lambda *args, **kw: CrossProfileBorrowProgress(
            "unknown", "unavailable", "blocked",
            "loan_result_not_confirmed",
        ),
    )
    outcome = checked(service, w)
    assert outcome.loan == "unknown" and outcome.transfer == "blocked"
    service.storage.placement_get.assert_not_called()
    service.materialize_to_identity.assert_not_called()


def test_bad_request_never_reads_storage_or_copies(scope):
    service, w = scope
    outcome = checked(service, w, reader=w.account.identity_id)
    assert outcome.reason_code == "lender_scope_unverified"
    service.storage.placement_get.assert_not_called()
    service.materialize_to_identity.assert_not_called()


def test_lender_context_rebind_during_sqlite_lookup_is_revoked(scope):
    service, w = scope
    def changed(_id):
        service._source_context_epoch += 1
        return {
            "identity_id": w.account.identity_id,
            "provider_id": w.account.provider_id,
            "source_item_id": w.notice.record_id,
            "state": "present", "artifact_id": "sha256:synthetic",
        }
    service.storage.placement_get.side_effect = changed
    outcome = checked(service, w)
    assert outcome.reason_code == "lender_delivery_unverified"
    service.materialize_to_identity.assert_not_called()
