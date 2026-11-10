"""#4987: exact-account lender placement discovery, read-only."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.borrow_for_identity_placement_resolution import (
    resolve_cross_profile_lender_placement as resolve,
)
from cifereca.core.borrow_for_identity_progress import CrossProfileBorrowProgress
from tests.test_core_p1_4662_cross_profile_borrow_preview import witness


@pytest.fixture
def case(monkeypatch):
    w = witness()
    loan = CrossProfileBorrowProgress(
        "confirmed", "unavailable", "blocked",
        "lender_loan_confirmed_transfer_unverified",
    )
    mock = Mock(return_value={
        "placement_id": "placed-A", "identity_id": "alice",
        "provider_id": "paris", "source_item_id": w.notice.record_id,
        "state": "present",
    })
    service = NS(
        current_identity=NS(identity_id="bob"),
        _account_context_epoch=7, _source_context_epoch=9,
        local_placement_for_source=mock,
        execute_cross_profile_borrow=Mock(side_effect=AssertionError("no borrow")),
        materialize_to_identity=Mock(side_effect=AssertionError("no send")),
    )
    loan_state = Mock(return_value=loan)
    monkeypatch.setattr(
        "cifereca.core.borrow_for_identity_placement_resolution."
        "observed_cross_profile_borrow_progress", loan_state,
    )
    return service, w, loan_state


def inspect(service, w):
    return resolve(
        service, requesting_identity_id="bob",
        borrowing_account=w.account, exact_notice=w.notice,
    )


def test_completed_unique_lender_acquisition_yields_only_inert_placement(case):
    service, w, progress = case
    result = inspect(service, w)
    assert (result.status, result.loan, result._placement_id) == (
        "present", "confirmed", "placed-A",
    )
    assert result.executable is result.retry_borrow is False
    service.local_placement_for_source.assert_called_once_with(
        provider_id="paris", credential_id=w.account.credential_id,
        item_id=w.notice.record_id, identity_id="alice",
        account_id=w.account.unique_id,
    )
    service.execute_cross_profile_borrow.assert_not_called()
    service.materialize_to_identity.assert_not_called()


@pytest.mark.parametrize("change", [
    {"identity_id": "bob"}, {"provider_id": "other"},
    {"source_item_id": "other"}, {"state": "absent"},
    {"placement_id": None}, {"placement_id": ""},
])
def test_other_or_incomplete_placement_never_yields_send_candidate(case, change):
    service, w, _ = case
    bad = service.local_placement_for_source.return_value.copy()
    bad.update(change)
    service.local_placement_for_source.return_value = bad
    result = inspect(service, w)
    assert result.status == "unavailable"
    assert result._placement_id is None and result.loan == "confirmed"
    service.materialize_to_identity.assert_not_called()


@pytest.mark.parametrize("loan_status", ["unknown", "not_attempted"])
def test_no_positive_claim_never_queries_acquisitions(case, loan_status):
    service, w, progress = case
    progress.return_value = CrossProfileBorrowProgress(
        loan_status, "unavailable", "blocked", "loan_result_not_confirmed",
    )
    result = inspect(service, w)
    assert result.status == "unavailable" and result.loan == loan_status
    service.local_placement_for_source.assert_not_called()
    service.materialize_to_identity.assert_not_called()


def test_missing_or_ambiguous_local_receipt_is_not_a_placement(case):
    service, w, _ = case
    service.local_placement_for_source.return_value = None
    result = inspect(service, w)
    assert result.status == "unavailable" and result._placement_id is None
    service.materialize_to_identity.assert_not_called()


def test_source_context_change_inside_local_reader_revokes_result(case):
    service, w, _ = case
    row = service.local_placement_for_source.return_value
    def mutate(**kw):
        service._source_context_epoch += 1
        return row
    service.local_placement_for_source.side_effect = mutate
    result = inspect(service, w)
    assert result.status == "unavailable"
    assert result._placement_id is None


def test_wrong_reader_or_notice_provider_rejected_without_local_reads(case):
    service, w, progress = case
    service.current_identity = NS(identity_id="another")
    assert inspect(service, w).status == "unavailable"
    service.current_identity = NS(identity_id="bob")
    assert inspect(service, replace(
        w, notice=replace(w.notice, source_provider="other"),
    )).status == "unavailable"
    progress.assert_not_called()
    service.local_placement_for_source.assert_not_called()
