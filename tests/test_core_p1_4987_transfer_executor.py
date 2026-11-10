"""#4987: cross-profile send reuses the canonical independent local copy."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.borrow_for_identity_transfer_executor import (
    send_cross_profile_borrowed_placement as send,
)
from cifereca.core.borrow_for_identity_transfer_preflight import CrossProfileTransferPreflight
from tests.test_core_p1_4662_cross_profile_borrow_preview import witness


@pytest.fixture
def setup_send(monkeypatch):
    w = witness()
    reader = NS(identity_id="bob")
    native = Mock(return_value={"status": "complete", "path": "/synthetic/bob/book.epub"})
    service = NS(
        current_identity=reader, _account_context_epoch=2,
        _source_context_epoch=5, materialize_to_identity=native,
    )
    preflight = Mock(return_value=CrossProfileTransferPreflight(
        "confirmed", "present", "ready_for_send",
        "lender_placement_present_send_available", executable=True,
    ))
    monkeypatch.setattr(
        "cifereca.core.borrow_for_identity_transfer_executor."
        "preflight_cross_profile_loan_transfer", preflight,
    )
    return service, w, preflight


def execute(service, w, *, confirmed=True):
    return send(
        service, requesting_identity_id="bob",
        borrowing_account=w.account, exact_notice=w.notice,
        lender_placement_id="placement-a", confirmed=confirmed,
    )


def test_explicit_confirmed_copy_uses_only_existing_native_send(setup_send):
    service, w, preflight = setup_send
    result = execute(service, w)
    assert (result["loan"], result["delivery"], result["transfer"]) == (
        "confirmed", "present", "completed",
    )
    assert result["status"] == "complete"
    assert result["retry_borrow"] is False
    service.materialize_to_identity.assert_called_once_with(
        "placement-a", "bob",
    )
    assert preflight.call_count == 1


def test_idempotent_native_unchanged_receipt_is_completed_copy(setup_send):
    service, w, _ = setup_send
    service.materialize_to_identity.return_value = {
        "status": "unchanged", "path": "/synthetic/bob/book.epub",
    }
    result = execute(service, w)
    assert result["status"] == "complete"
    assert result["transfer"] == "completed"
    service.materialize_to_identity.assert_called_once_with("placement-a", "bob")


def test_missing_copy_confirmation_never_inspects_or_sends(setup_send):
    service, w, preflight = setup_send
    result = execute(service, w, confirmed=False)
    assert result["status"] == "blocked"
    assert result["reason_code"] == "transfer_confirmation_required"
    preflight.assert_not_called()
    service.materialize_to_identity.assert_not_called()


@pytest.mark.parametrize("assessment", [
    CrossProfileTransferPreflight(
        "unknown", "unavailable", "blocked", "loan_result_not_confirmed"),
    CrossProfileTransferPreflight(
        "confirmed", "unavailable", "blocked", "lender_delivery_unverified"),
])
def test_unconfirmed_loan_or_missing_placement_never_sends(
    setup_send, assessment,
):
    service, w, preflight = setup_send
    preflight.return_value = assessment
    result = execute(service, w)
    assert result["transfer"] == "blocked" and result["retry_borrow"] is False
    service.materialize_to_identity.assert_not_called()


@pytest.mark.parametrize("result", [
    None, {"status": "pending"}, {"status": "failed"}, {},
])
def test_incomplete_native_send_receipt_never_claims_copy_completed(
    setup_send, result,
):
    service, w, _ = setup_send
    service.materialize_to_identity.return_value = result
    state = execute(service, w)
    assert state["transfer"] == "blocked"
    assert state["reason_code"] == "transfer_result_unverified"
    assert state["retry_borrow"] is False


def test_native_send_exception_keeps_loan_without_retry(setup_send):
    service, w, _ = setup_send
    service.materialize_to_identity.side_effect = RuntimeError("private-secret")
    result = execute(service, w)
    assert result["transfer"] == "blocked"
    assert result["loan"] == "confirmed"
    assert result["delivery"] == "present"
    assert "private-secret" not in repr(result)
    assert result["retry_borrow"] is False


def test_context_rebound_during_preflight_does_not_send(setup_send):
    service, w, preflight = setup_send
    original = preflight.return_value
    def revoke(*args, **kwargs):
        service._source_context_epoch += 1
        return original
    preflight.side_effect = revoke
    result = execute(service, w)
    assert result["reason_code"] == "transfer_context_revoked"
    service.materialize_to_identity.assert_not_called()


def test_wrong_reader_context_does_not_send(setup_send):
    service, w, preflight = setup_send
    service.current_identity = NS(identity_id="another")
    result = execute(service, w)
    assert result["status"] == "blocked"
    preflight.assert_not_called()
    service.materialize_to_identity.assert_not_called()


def test_native_core_send_produces_independent_copy_and_idempotent_replay(
    tmp_path, monkeypatch,
):
    """Exercise the real LibraryService and native send, not a mocked copy."""
    import os
    from dataclasses import replace
    from pathlib import Path

    from tests.test_core0925_open_destinations import _service
    from tests.test_core_p1_3275_safe_transfer import _source
    from cifereca.core.borrow_for_identity_progress import CrossProfileBorrowProgress

    service = _service(tmp_path)
    source, placement = _source(service, tmp_path)
    service.current_identity = service.identity_by_id("bob")
    original = witness("alice", record="book")
    account = replace(original.account, provider_id="local")
    notice = replace(
        original.notice, source_provider="local", record_id="book",
    )
    monkeypatch.setattr(
        "cifereca.core.borrow_for_identity_transfer_preflight."
        "observed_cross_profile_borrow_progress",
        lambda *args, **kw: CrossProfileBorrowProgress(
            "confirmed", "unavailable", "blocked",
            "lender_loan_confirmed_transfer_unverified",
        ),
    )
    try:
        first = service.send_cross_profile_borrowed_placement(
            requesting_identity_id="bob", borrowing_account=account,
            exact_notice=notice, lender_placement_id=placement, confirmed=True,
        )
        assert first["status"] == "complete", first
        assert first["transfer"] == "completed"
        received = [
            row for row in service.storage.placements()
            if row.get("identity_id") == "bob"
            and (row.get("metadata") or {}).get("materialized_from") == placement
        ]
        assert len(received) == 1
        target = Path(received[0]["path"])
        assert target.read_bytes() == source.read_bytes()
        assert os.stat(target).st_ino != os.stat(source).st_ino
        assert service.storage.entitlements() == []
        again = service.send_cross_profile_borrowed_placement(
            requesting_identity_id="bob", borrowing_account=account,
            exact_notice=notice, lender_placement_id=placement, confirmed=True,
        )
        assert again["status"] == "complete"
        assert again["transfer"] == "completed"
        received_again = [
            row for row in service.storage.placements()
            if row.get("identity_id") == "bob"
            and (row.get("metadata") or {}).get("materialized_from") == placement
        ]
        assert len(received_again) == 1
    finally:
        service.close()
