"""P1 #5136: scoped read-only post-borrow verification for a typed provider.

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


def verify_borrow_without_submission(
    service, item: SearchResult, *, expected_identity_id: str,
    expected_account: Account, expected_operation_id: str,
    expected_provider_id: str, expected_docbase: str,
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
            or type(expected_provider_id) is not str or not expected_provider_id
            or type(expected_docbase) is not str or not expected_docbase
            or item.source_provider != expected_provider_id
            or item.docbase != expected_docbase
            or item.medium != "digital" or not item.record_id
            or expected_account.identity_id != expected_identity_id
            or expected_account.provider_id != expected_provider_id
            or expected_account.credential_id != item.source_credential
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
        session = service._session_for_source(expected_provider_id, item.source_credential)
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
        if not lease.current(service) or not checkpoint_current():
            return result("blocked", "borrow_verification_source_changed")
        if (type(observed) is not ProviderRightsSnapshot
                or observed.scope != ProviderRightsScope(
                    expected_identity_id, expected_provider_id,
                    item.source_credential, expected_account.unique_id,
                )
                or observed.branch != expected_docbase.casefold()):
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


def _followup_readiness(reader, item: SearchResult) -> bool:
    """Use the provider's *existing* local-only diagnostic, not an import."""
    if not callable(reader):
        return False
    try:
        state = reader(item)
        return (type(state) is dict
                and state.get("reason_code") == "local_previous_attempt"
                and state.get("local_action_possible") is False)
    except Exception:
        return False


def _pending_borrow_followup_scope(service, item: SearchResult, *, readiness):
    """Return one private exact claim, never an operation reference to the UI.

    This is a read-only lookup of a previously *consumed* borrow attempt.
    No fresh claim, GET, POST, provider permission or source remapping.
    """
    from .borrow_action_scope import borrow_claim_key
    from .loan_action_scope import _inert_session_accounts, _inert_session_state

    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    if (type(item) is not SearchResult
            or type(identity) is not str or not identity
            or item.medium != "digital"
            or type(item.docbase) is not str or not item.docbase
            or type(item.source_provider) is not str or not item.source_provider
            or not _followup_readiness(readiness, item)):
        return None
    try:
        session = service._session_for_source(item.source_provider, item.source_credential)
        registered = _inert_session_accounts(session)
        state = _inert_session_state(session)
        if (registered is None or state is None
                or state.get("connected") is not True
                or state.get("_closed") is True):
            return None
        accounts = [
            row for row in service.accounts
            if (type(row) is Account and row.is_primary is True
                and row.unique_id == "" and row.identity_id == identity
                and row.provider_id == item.source_provider
                and row.credential_id == item.source_credential
                and sum(member is row for member in registered) == 1)
        ]
        if len(accounts) != 1:
            return None
        account = accounts[0]
        key = borrow_claim_key(account, item)
        if key is None:
            return None
        checkpoint = service.storage.loan_borrow_state(key)
        if (type(checkpoint) is not dict
                or checkpoint.get("status") != "verification_unavailable"):
            return None
        operation_id = checkpoint.get("operation_id")
        if (type(operation_id) is not str
                or not 1 <= len(operation_id) <= 128
                or any(ord(c) < 33 or ord(c) > 126 for c in operation_id)):
            return None
        return account, operation_id
    except Exception:
        return None


def borrow_followup_available(service, item: SearchResult, *, readiness) -> bool:
    """Inert local UI admission for one pending Paris loan check after restart."""
    return _pending_borrow_followup_scope(service, item, readiness=readiness) is not None


def verify_pending_borrow_followup(service, item: SearchResult, *, readiness) -> dict[str, object]:
    """Re-read a prior uncertain claim, never initiate or clear a loan request.

    The one-use operation ID is resolved internally from the exact durable
    checkpoint. The public result contains a closed status/reason only.
    """
    def blocked():
        return {
            "status": "blocked", "reason_code": "borrow_verification_claim_mismatch",
            "retry_borrow": False, "mutated": False,
        }

    pinned = _pending_borrow_followup_scope(service, item, readiness=readiness)
    if pinned is None:
        return blocked()
    account, operation_id = pinned
    result = service.verify_borrow_without_submission(
        item, expected_identity_id=account.identity_id,
        expected_account=account, expected_operation_id=operation_id,
        expected_provider_id=item.source_provider, expected_docbase=item.docbase,
    )
    # An account can be disabled or replaced during provider I/O, even when
    # the underlying legacy session object remains registered. Never publish
    # a positive result after that change.
    current = _pending_borrow_followup_scope(service, item, readiness=readiness)
    if (current is None or current[0] is not account
            or current[1] != operation_id
            or not _followup_readiness(readiness, item)):
        return blocked()
    if (type(result) is not dict
            or result.get("status") not in {
                "blocked", "unavailable", "observed_current_loan",
            }
            or result.get("retry_borrow") is not False
            or result.get("mutated") is not False):
        return blocked()
    return {
        "status": result["status"],
        "reason_code": (
            result.get("reason_code")
            if type(result.get("reason_code")) is str
            and result["reason_code"] in {
                "borrow_verification_scope_invalid",
                "borrow_verification_claim_mismatch",
                "borrow_verification_source_changed",
                "borrow_verification_observation_invalid",
                "borrow_verification_observation_partial",
                "borrow_verification_loan_unconfirmed",
                "borrow_verification_current_loan_observed",
                "borrow_verification_observation_unavailable",
            }
            else "borrow_verification_observation_unavailable"
        ),
        "retry_borrow": False,
        "mutated": False,
    }


__all__ = ["verify_borrow_without_submission",
           "borrow_followup_available", "verify_pending_borrow_followup"]
