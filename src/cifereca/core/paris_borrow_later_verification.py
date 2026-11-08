"""P1 #5136: explicit, read-only post-borrow verification for Paris.

A consumed manual borrow claim is never reset here. An observed loan proves
its present inventory claim, not that this application created it, nor that an
ACSM or final file was delivered. The provider owns the only upstream GET.
"""
from __future__ import annotations

from ..models import Account, SearchResult
from ..plugin_api.provider_rights_observation import (
    ProviderRightsScope, ProviderRightsSnapshot,
)
from .borrow_action_scope import borrow_claim_key, capture_borrow_action_scope
from .manual_paris_borrow import _normal_paris_source_current


def verify_paris_borrow_without_submission(
    service, item: SearchResult, *, expected_identity_id: str,
    expected_account: Account, expected_operation_id: str,
) -> dict[str, object]:
    """Check one prior uncertain operation; never send or permit another POST."""
    def result(status: str, reason: str) -> dict[str, object]:
        return {
            "status": status, "reason_code": reason,
            "retry_borrow": False, "mutated": False,
        }

    if (type(item) is not SearchResult or type(expected_account) is not Account
            or type(expected_identity_id) is not str or not expected_identity_id
            or type(expected_operation_id) is not str
            or not 0 < len(expected_operation_id) <= 128
            or item.source_provider != "paris" or item.docbase != "DILICOM"
            or item.medium != "digital" or not item.record_id
            or expected_account.identity_id != expected_identity_id
            or expected_account.provider_id != "paris"
            or expected_account.credential_id != item.source_credential
            or expected_account.unique_id != ""
            or expected_account.is_primary is not True):
        return result("blocked", "borrow_verification_scope_invalid")

    key = borrow_claim_key(expected_account, item)
    if key is None:
        return result("blocked", "borrow_verification_scope_invalid")

    def checkpoint_current() -> bool:
        # A malformed or absent claim never becomes permission to inspect
        # another operation, and a new claim cannot impersonate this attempt.
        row = service.storage.loan_borrow_state(key)
        return (type(row) is dict
                and row.get("status") == "verification_unavailable"
                and row.get("operation_id") == expected_operation_id)

    try:
        if not checkpoint_current():
            return result("blocked", "borrow_verification_claim_mismatch")
        if not _normal_paris_source_current(service, item, expected_identity_id):
            return result("blocked", "borrow_verification_source_changed")
        session = service._session_for_source("paris", item.source_credential)
        if (sum(account is expected_account for account in service.accounts) != 1
                or sum(account is expected_account
                       for account in session._accounts) != 1):
            return result("blocked", "borrow_verification_source_changed")
        lease = capture_borrow_action_scope(
            service, item, expected_account, session,
            expected_identity_id=expected_identity_id,
        )
        if lease is None or not checkpoint_current():
            return result("blocked", "borrow_verification_source_changed")

        # Only the producer's read-only typed contract is admitted here.
        # No borrow_explicit_with_claim, PNB URL retrieval, ACSM staging,
        # manual loan claim, or write to any SQLite table occurs.
        observed = session.rights_observation_snapshot(expected_account)
        if (not lease.current(service) or not checkpoint_current()
                or not _normal_paris_source_current(
                    service, item, expected_identity_id,
                )):
            return result("blocked", "borrow_verification_source_changed")
        if (type(observed) is not ProviderRightsSnapshot
                or observed.scope != ProviderRightsScope(
                    expected_identity_id, "paris", item.source_credential, "",
                )
                or observed.branch != "dilicom"):
            return result("unavailable", "borrow_verification_observation_invalid")
        if not observed.claims_complete_branch:
            return result("unavailable", "borrow_verification_observation_partial")
        matches = [
            row for row in observed.rights
            if row.edition_id == item.record_id and row.kind == "loan"
            and row.provider_attested and row.state != "inactive"
        ]
        if len(matches) != 1:
            # Even an empty complete inventory must not release a consumed
            # request or claim the loan was not created by the prior POST.
            return result("unavailable", "borrow_verification_loan_unconfirmed")
        return result("observed_current_loan", "borrow_verification_current_loan_observed")
    except Exception:
        return result("unavailable", "borrow_verification_observation_unavailable")


__all__ = ["verify_paris_borrow_without_submission"]
