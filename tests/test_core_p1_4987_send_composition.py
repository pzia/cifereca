"""#4987: resolve-and-send composition never repeats supplier borrowing."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.borrow_for_identity_send_composition import (
    send_confirmed_lender_local_copy as send,
)
from cifereca.core.borrow_for_identity_placement_resolution import LenderLocalPlacement
from tests.test_core_p1_4662_cross_profile_borrow_preview import witness


@pytest.fixture
def environment(monkeypatch):
    w = witness()
    resolve = Mock(return_value=LenderLocalPlacement(
        "present", "lender_exact_local_placement_present",
        _placement_id="placement-a", loan="confirmed",
    ))
    native = Mock(return_value={
        "status": "complete", "loan": "confirmed", "delivery": "present",
        "transfer": "completed",
        "reason_code": "independent_local_copy_completed",
        "retry_borrow": False,
    })
    service = NS(
        current_identity=NS(identity_id="bob"),
        _account_context_epoch=4, _source_context_epoch=5,
        send_cross_profile_borrowed_placement=native,
        execute_cross_profile_borrow=Mock(side_effect=AssertionError("no POST")),
    )
    monkeypatch.setattr(
        "cifereca.core.borrow_for_identity_send_composition."
        "resolve_cross_profile_lender_placement", resolve,
    )
    return service, w, resolve


def execute(service, w, *, confirmed=True):
    return send(
        service, requesting_identity_id="bob", borrowing_account=w.account,
        exact_notice=w.notice, confirmed=confirmed,
    )


def test_native_copy_after_exact_local_resolution(environment):
    service, w, resolver = environment
    result = execute(service, w)
    assert (result["status"], result["loan"], result["transfer"]) == (
        "complete", "confirmed", "completed",
    )
    resolver.assert_called_once_with(
        service, requesting_identity_id="bob",
        borrowing_account=w.account, exact_notice=w.notice,
    )
    service.send_cross_profile_borrowed_placement.assert_called_once_with(
        requesting_identity_id="bob", borrowing_account=w.account,
        exact_notice=w.notice, lender_placement_id="placement-a",
        confirmed=True,
    )
    service.execute_cross_profile_borrow.assert_not_called()


def test_no_explicit_send_consent_never_reads_or_copies(environment):
    service, w, resolver = environment
    assert execute(service, w, confirmed=False)["status"] == "blocked"
    resolver.assert_not_called()
    service.send_cross_profile_borrowed_placement.assert_not_called()


@pytest.mark.parametrize("resolution", [
    LenderLocalPlacement("unavailable", "loan_result_not_confirmed"),
    LenderLocalPlacement("unavailable", "lender_delivery_unverified",
                         loan="confirmed"),
    LenderLocalPlacement("present", "bad", _placement_id="",
                         loan="confirmed"),
    LenderLocalPlacement("present", "bad", _placement_id="placement-a",
                         loan="confirmed", executable=True),
])
def test_unverified_or_forced_resolution_never_sends(environment, resolution):
    service, w, resolver = environment
    resolver.return_value = resolution
    result = execute(service, w)
    assert result["transfer"] == "blocked"
    assert result["retry_borrow"] is False
    service.send_cross_profile_borrowed_placement.assert_not_called()
    service.execute_cross_profile_borrow.assert_not_called()


def test_context_rebind_between_resolve_and_send_is_revoked(environment):
    service, w, resolver = environment
    original = resolver.return_value
    def changed(*args, **kw):
        service.current_identity = NS(identity_id="bob")
        return original
    resolver.side_effect = changed
    result = execute(service, w)
    assert result["reason_code"] == "transfer_context_revoked"
    service.send_cross_profile_borrowed_placement.assert_not_called()


@pytest.mark.parametrize("native", [
    {"status": "complete", "loan": "unknown",
     "delivery": "present", "transfer": "completed",
     "reason_code": "independent_local_copy_completed", "retry_borrow": False},
    {"status": "complete", "loan": "confirmed",
     "delivery": "present", "transfer": "completed",
     "reason_code": "independent_local_copy_completed", "retry_borrow": True},
    {"status": "blocked", "loan": "confirmed", "delivery": "present",
     "transfer": "blocked", "reason_code": "transfer_result_unverified",
     "retry_borrow": False},
    None,
])
def test_unexpected_or_failed_native_result_never_claims_success(environment, native):
    service, w, _ = environment
    service.send_cross_profile_borrowed_placement.return_value = native
    result = execute(service, w)
    assert result["transfer"] == "blocked"
    assert result["retry_borrow"] is False
    service.execute_cross_profile_borrow.assert_not_called()


def test_native_exception_never_leaks_secret_or_reborrows(environment):
    service, w, _ = environment
    service.send_cross_profile_borrowed_placement.side_effect = RuntimeError(
        "account-private-barcode",
    )
    result = execute(service, w)
    assert result["status"] == "blocked"
    assert "account-private-barcode" not in repr(result)
    service.execute_cross_profile_borrow.assert_not_called()
