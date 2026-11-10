"""#4988: discovery of *existing* loan continuation is strictly read-only."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.borrow_for_identity_existing_loan import (
    existing_lender_loan_candidates as recover,
)
from cifereca.core.borrow_for_identity_progress import CrossProfileBorrowProgress
from cifereca.core.borrow_for_identity_placement_resolution import LenderLocalPlacement
from tests.test_core_p1_4662_cross_profile_borrow_preview import witness


@pytest.fixture
def case(monkeypatch):
    w = witness()
    verified = CrossProfileBorrowProgress(
        "confirmed", "unavailable", "blocked",
        "lender_loan_confirmed_transfer_unverified",
    )
    loan = Mock(return_value=verified)
    placement = Mock(return_value=LenderLocalPlacement(
        "present", "lender_exact_local_placement_present",
        _placement_id="A-placement", loan="confirmed",
    ))
    monkeypatch.setattr(
        "cifereca.core.borrow_for_identity_existing_loan."
        "observed_cross_profile_borrow_progress", loan,
    )
    monkeypatch.setattr(
        "cifereca.core.borrow_for_identity_existing_loan."
        "resolve_cross_profile_lender_placement", placement,
    )
    service = NS(
        current_identity=NS(identity_id="bob"),
        _account_context_epoch=2, _source_context_epoch=5,
        accounts=[w.account],
        execute_cross_profile_borrow=Mock(
            side_effect=AssertionError("never POST")),
        materialize_to_identity=Mock(
            side_effect=AssertionError("never copy")),
    )
    return service, w, loan, placement


def test_existing_confirmed_claim_with_local_file_can_resume_without_post(case):
    service, w, loan, placement = case
    rows = recover(service, exact_notice=w.notice)
    assert len(rows) == 1
    assert rows[0]._account is w.account
    assert rows[0].status == "present"
    assert rows[0].loan == "confirmed"
    assert rows[0].executable is rows[0].retry_borrow is False
    assert w.account.credential_id not in repr(rows[0])
    loan.assert_called_once_with(
        service, requesting_identity_id="bob",
        borrowing_account=w.account, exact_notice=w.notice,
    )
    placement.assert_called_once_with(
        service, requesting_identity_id="bob",
        borrowing_account=w.account, exact_notice=w.notice,
    )
    service.execute_cross_profile_borrow.assert_not_called()
    service.materialize_to_identity.assert_not_called()


def test_loan_confirmed_before_local_delivery_can_be_rechecked_later(case):
    service, w, _loan, placement = case
    placement.return_value = LenderLocalPlacement(
        "unavailable", "lender_delivery_unverified", loan="confirmed",
    )
    rows = recover(service, exact_notice=w.notice)
    assert len(rows) == 1 and rows[0].status == "awaiting_delivery"
    service.execute_cross_profile_borrow.assert_not_called()


@pytest.mark.parametrize("loan_state", ["unknown", "not_attempted"])
def test_no_verified_positive_loan_never_looks_up_placement(case, loan_state):
    service, w, loan, placement = case
    loan.return_value = CrossProfileBorrowProgress(
        loan_state, "unavailable", "blocked", "loan_result_not_confirmed",
    )
    assert recover(service, exact_notice=w.notice) == ()
    placement.assert_not_called()


def test_other_credential_or_item_scope_never_reads_loan_receipt(case):
    service, w, loan, placement = case
    service.accounts = [replace(w.account, credential_id="other")]
    assert recover(service, exact_notice=w.notice) == ()
    loan.assert_not_called()
    placement.assert_not_called()


def test_duplicate_primary_lender_accounts_refuse_to_choose_by_order(case):
    service, w, loan, placement = case
    service.accounts.append(w.account)
    assert recover(service, exact_notice=w.notice) == ()
    loan.assert_not_called()
    placement.assert_not_called()


def test_source_rebind_during_placement_read_revokes_all_candidates(case):
    service, w, _loan, placement = case
    actual = placement.return_value

    def rebind(*args, **kwargs):
        service._source_context_epoch += 1
        return actual

    placement.side_effect = rebind
    assert recover(service, exact_notice=w.notice) == ()
    service.execute_cross_profile_borrow.assert_not_called()


def test_reader_object_rebind_during_loan_read_revokes_all_candidates(case):
    service, w, loan, _placement = case
    result = loan.return_value

    def rebind(*args, **kwargs):
        service.current_identity = NS(identity_id="bob")
        return result

    loan.side_effect = rebind
    assert recover(service, exact_notice=w.notice) == ()


def test_claim_metadata_is_never_exposed_or_posted(case):
    service, w, _, placement = case
    placement.return_value = LenderLocalPlacement(
        "present", "lender_exact_local_placement_present",
        _placement_id="/private/secret-acsm", loan="confirmed",
    )
    rows = recover(service, exact_notice=w.notice)
    assert len(rows) == 1
    assert "/private/secret-acsm" not in repr(rows)
    assert not hasattr(rows[0], "_placement_id")
    service.materialize_to_identity.assert_not_called()


def test_current_reader_is_not_presented_as_foreign_lender(case):
    service, w, loan, _placement = case
    service.current_identity = NS(identity_id=w.account.identity_id)
    assert recover(service, exact_notice=w.notice) == ()
    loan.assert_not_called()



def test_real_core_recovers_paris_confirmed_claim_after_late_local_delivery(tmp_path):
    """Paris HTTP is simulated; SQLite Core state and local FS are real."""
    import hashlib

    from tests.test_core_p1_4987_cross_profile_paris_http import (
        _cross_profile_fixture, _execute,
    )

    service, account, notice, offer, preview, http = _cross_profile_fixture(
        tmp_path, result="no-acsm",
    )
    try:
        assert _execute(service, offer, preview)["status"] == "confirmed"
        assert http["mutations"] == 1
        waiting = service.existing_lender_loan_candidates(exact_notice=notice)
        assert len(waiting) == 1, waiting
        assert waiting[0].status == "awaiting_delivery"
        assert waiting[0]._account is account
        # The fixture's publisher is synthetic; this is NOT proof that an
        # actual Paris supplier has delivered bytes or a DRM licence.
        payload = b"%PDF-1.7\\nSynthetic previously delivered loan\\n"
        digest = hashlib.sha256(payload).hexdigest()
        source = tmp_path / "EBooks" / "alex" / "paris" / "Recovery.pdf"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(payload)
        artifact_id = "sha256:" + digest
        service.storage.artifact_put({
            "artifact_id": artifact_id, "sha256": digest,
            "size": len(payload), "media_format": "pdf",
        })
        service.storage.placement_put({
            "identity_id": account.identity_id,
            "provider_id": account.provider_id,
            "source_item_id": notice.record_id,
            "artifact_id": artifact_id, "path": str(source),
            "state": "present", "metadata": {},
        })
        service.storage.acquisition_put({
            "acquisition_id": "synthetic-late-paris-delivery",
            "identity_id": account.identity_id,
            "provider_id": account.provider_id,
            "credential_id": account.credential_id,
            "account_id": account.unique_id,
            "item_id": notice.record_id,
            "status": "complete", "artifact_id": artifact_id,
            "metadata": {"provider_loan_id": "loan-exact"},
        })
        resumed = service.existing_lender_loan_candidates(exact_notice=notice)
        assert len(resumed) == 1, resumed
        assert resumed[0].status == "present"
        assert resumed[0]._account is account
        assert resumed[0].executable is False
        assert resumed[0].retry_borrow is False
        assert http["mutations"] == 1
    finally:
        service.close()
