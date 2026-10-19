"""Read-only Paris borrower provenance and cross-identity ledger checks.

The service facade retains profile mutation, locks and all acquisition/return
entry points. This boundary reads existing account and binding evidence only;
it neither acquires an ACSM nor chooses an unproven Adobe profile."""
from __future__ import annotations

from typing import Any
from ..models import Loan


# This read-only resolver emits exactly these four refusal codes. A caller
# cannot use an arbitrary plugin-provided object/string as a public status,
# durable ledger attention_code, or reason to bypass the grant check.
_BORROWER_REFUSAL_CODES = frozenset({
    "adept_borrower_ambiguous",
    "adept_borrower_binding_required",
    "adept_borrower_connection_changed",
    "adept_borrower_profile_changed",
})


def public_borrower_refusal_code(value: Any) -> str:
    return (value if type(value) is str and value in _BORROWER_REFUSAL_CODES
            else "adept_borrower_ambiguous")


def _paris_adept_identity_for_loan(self, loan: Loan, *, connection_identity_id: str) -> tuple[str | None, str | None]:
    """Resolve the Adobe borrower separately from the Paris connection.

    The primary account normally uses its proven connection identity.
    An explicit exceptional grant takes precedence without changing that
    identity's normal profile. Linked accounts always require a grant.
    """
    if loan.source_account_id is None:
        return None, "adept_borrower_ambiguous"
    borrower = str(loan.source_account_id).strip()
    credential = str(loan.source_credential or "").strip()
    if not credential:
        return None, "adept_borrower_ambiguous"
    matches = [
        account for account in self.accounts
        if str(account.provider_id).lower() == "paris"
        and str(account.credential_id) == credential
        and (
            (borrower == "primary" and account.is_primary)
            or str(account.unique_id or "").strip() == borrower
        )
    ]
    if len(matches) != 1 or str(matches[0].identity_id) != connection_identity_id:
        return None, "adept_borrower_ambiguous"
    if matches[0].is_primary:
        if self._borrower_identity_conflict(
            self.accounts, provider_id="paris",
            account_id=borrower, identity_id=connection_identity_id,
            credential_id=credential,
        ):
            return None, "adept_borrower_ambiguous"
        borrower = "primary"

    if not matches[0].is_primary and (not borrower or borrower == "primary"):
        return None, "adept_borrower_ambiguous"
    binding = self.storage.paris_borrower_adept_binding_get(
        credential_id=credential, account_id=borrower,
    )
    if binding is None:
        if matches[0].is_primary:
            return connection_identity_id, None
        return None, "adept_borrower_binding_required"
    if str(binding.get("connection_identity_id") or "") != connection_identity_id:
        return None, "adept_borrower_connection_changed"
    owner = str(binding.get("borrower_identity_id") or "")
    pinned = str(binding.get("profile_id") or "")
    if not owner or self.identity_by_id(owner) is None or not pinned:
        return None, "adept_borrower_profile_changed"
    current = self.storage.adept_profile_binding_get("identity", owner)
    if current != pinned or binding.get("confirmed_by") != "explicit":
        return None, "adept_borrower_profile_changed"
    return owner, None


def _cross_identity_loan_conflict(acquisitions: Any, *, provider_loan_id: str, identity_id: str, entitlements: Any=()) -> bool:
    """Reject a Paris loan recorded under another identity in either ledger.

    A currently active entitlement is also a trace of the same upstream
    loan; it does not prove that identity is the rightful ADEPT owner.
    """
    loan_key = str(provider_loan_id or "").strip()
    if not loan_key:
        return True
    acquired_elsewhere = any(
        str((row.get("metadata") or {}).get("provider_loan_id") or "").strip() == loan_key
        and str(row.get("identity_id") or "") != identity_id
        and str(row.get("strategy") or "") == "loan_auto"
        for row in acquisitions
    )
    return acquired_elsewhere or any(
        str(row.get("loan_id") or "").strip() == loan_key
        and str(row.get("identity_id") or "") != identity_id
        for row in entitlements
    )
