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
    # A malformed provider loan cannot attest a borrower by coercing
    # arbitrary objects (or str subclasses) into a stored account key.
    # Native strings retain the exact historical trim and primary semantics.
    if (type(loan.source_account_id) is not str
            or type(loan.source_credential) is not str):
        return None, "adept_borrower_ambiguous"
    borrower = loan.source_account_id.strip()
    credential = loan.source_credential.strip()
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
    # This is a stored *grant*, not an arbitrary provider projection. Never
    # coerce malformed values or call custom equality/getter implementations
    # to establish borrower ownership or the pinned ADEPT profile.
    if type(binding) is not dict:
        return None, "adept_borrower_profile_changed"
    bound_connection = binding.get("connection_identity_id")
    if type(bound_connection) is not str or bound_connection != connection_identity_id:
        return None, "adept_borrower_connection_changed"
    owner = binding.get("borrower_identity_id")
    pinned = binding.get("profile_id")
    if (type(owner) is not str or not owner
            or type(pinned) is not str or not pinned
            or self.identity_by_id(owner) is None):
        return None, "adept_borrower_profile_changed"
    current = self.storage.adept_profile_binding_get("identity", owner)
    confirmed_by = binding.get("confirmed_by")
    if (type(current) is not str or current != pinned
            or type(confirmed_by) is not str or confirmed_by != "explicit"):
        return None, "adept_borrower_profile_changed"
    return owner, None


def _cross_identity_loan_conflict(
    acquisitions: Any, *, provider_loan_id: str, identity_id: str,
    entitlements: Any=(), credential_id: str | None=None,
) -> bool:
    """Block foreign Paris loans within a provably matching connection.

    Upstream loan IDs are not unique across independent Paris credentials.
    Two different *attested* credential IDs cannot establish a cross-owner
    conflict by loan ID alone. Missing or malformed historical credentials
    stay ambiguous and therefore block. An account mismatch under the same
    credential is NOT sufficient to bypass the borrower/ADEPT guard.
    """

    def disjoint_connection(row: Any) -> bool:
        stored = row.get("credential_id")
        return (
            type(credential_id) is str and bool(credential_id)
            and type(stored) is str and bool(stored)
            and stored != credential_id
        )
    loan_key = str(provider_loan_id or "").strip()
    if not loan_key:
        return True
    acquired_elsewhere = any(
        str((row.get("metadata") or {}).get("provider_loan_id") or "").strip() == loan_key
        and str(row.get("identity_id") or "") != identity_id
        and str(row.get("strategy") or "") == "loan_auto"
        and not disjoint_connection(row)
        for row in acquisitions
    )
    return acquired_elsewhere or any(
        str(row.get("loan_id") or "").strip() == loan_key
        and str(row.get("identity_id") or "") != identity_id
        and not disjoint_connection(row)
        for row in entitlements
    )
