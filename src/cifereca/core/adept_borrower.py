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


def borrower_identity_for_loan(
    self: Any, loan: Loan, *, connection_identity_id: str,
) -> tuple[str | None, str | None]:
    """Use the one existing ADEPT borrower policy at both loan boundaries.

    Only the Paris legacy loan path has an exceptional borrower resolver;
    other source loans retain their proven connection identity. This is
    routing, never an implicit ADEPT grant or evidence of loan eligibility.
    """
    if (type(loan.source_provider) is not str or not loan.source_provider
            or type(connection_identity_id) is not str
            or not connection_identity_id):
        return None, "adept_borrower_ambiguous"
    if loan.source_provider.casefold() != "paris":
        return connection_identity_id, None
    return self._paris_adept_identity_for_loan(
        loan, connection_identity_id=connection_identity_id,
    )


def _paris_adept_identity_for_loan(self, loan: Loan, *, connection_identity_id: str) -> tuple[str | None, str | None]:
    """Resolve the Adobe borrower separately from the Paris connection.

    The primary account normally uses its proven connection identity.
    An explicit exceptional grant takes precedence without changing that
    identity's normal profile. Linked accounts always require a grant.
    """
    # A malformed provider loan cannot attest a borrower by coercing
    # arbitrary objects (or str subclasses) into a stored account key.
    # Native strings retain the exact historical trim and primary semantics.
    if (type(loan.source_provider) is not str or not loan.source_provider
            or type(loan.source_account_id) is not str
            or type(loan.source_credential) is not str):
        return None, "adept_borrower_ambiguous"
    # Canonicalize an explicitly supplied source once: storage conflict keys
    # must not become case-sensitive aliases for the same provider.
    source_provider = loan.source_provider.casefold()
    borrower = loan.source_account_id.strip()
    credential = loan.source_credential.strip()
    if not credential:
        return None, "adept_borrower_ambiguous"
    # Account rows can also originate in a provider plugin. Only native
    # coordinates may attest the source: never invoke untrusted __str__,
    # __eq__ or subclass methods while selecting ADEPT grant ownership.
    matches = [
        account for account in self.accounts
        if type(account.provider_id) is str
        and account.provider_id.casefold() == source_provider
        and type(account.credential_id) is str
        and account.credential_id == credential
        and type(account.is_primary) is bool
        and (
            (borrower == "primary" and account.is_primary)
            or (type(account.unique_id) is str
                and account.unique_id.strip() == borrower)
        )
    ]
    if (len(matches) != 1
            or type(matches[0].identity_id) is not str
            or matches[0].identity_id != connection_identity_id):
        return None, "adept_borrower_ambiguous"
    if matches[0].is_primary:
        if self._borrower_identity_conflict(
            self.accounts, provider_id=source_provider,
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
    if binding is not None:
        # During the explicit reconfirmation transition, a concurrent or
        # historically reintroduced legacy row must not silently override a
        # different v16 owner. Neither source of evidence may pick the first
        # identity when the two persisted grants disagree.
        try:
            v16 = self.storage.adept_borrower_grant_get(
                provider_id=source_provider, credential_id=credential,
                account_id=borrower,
            )
        except Exception:
            return None, "adept_borrower_ambiguous"
        if v16 is not None:
            if type(v16) is not dict:
                return None, "adept_borrower_profile_changed"
            try:
                v16_owner, v16_reason = self.explicit_adept_borrower_grant_for_source(
                    provider_id=source_provider, credential_id=credential,
                    account_id=borrower,
                    connection_identity_id=connection_identity_id,
                )
            except Exception:
                return None, "adept_borrower_ambiguous"
            if v16_reason is not None:
                return None, public_borrower_refusal_code(v16_reason)
            if (type(v16_owner) is not str or not v16_owner
                    or type(binding) is not dict
                    or type(binding.get("borrower_identity_id")) is not str
                    or binding["borrower_identity_id"] != v16_owner
                    or type(binding.get("profile_id")) is not str
                    or binding["profile_id"] != v16.get("profile_id")):
                return None, "adept_borrower_profile_changed"
    if binding is None:
        # The v16 register is independent of the historical Paris grant.
        # This path is positive-only: a linked borrower may be resolved from
        # newly confirmed, persisted source evidence, never an entitlement,
        # a GTK selection, or a missing legacy row alone.
        try:
            owner, reason = self.explicit_adept_borrower_grant_for_source(
                provider_id=source_provider,
                credential_id=credential,
                account_id=borrower,
                connection_identity_id=connection_identity_id,
            )
        except Exception:
            return None, "adept_borrower_ambiguous"
        if reason is None:
            if (type(owner) is not str or not owner
                    or self.identity_by_id(owner) is None):
                return None, "adept_borrower_ambiguous"
            return owner, None
        if reason != "adept_borrower_binding_required":
            return None, public_borrower_refusal_code(reason)
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
    entitlements: Any = (),
) -> bool:
    """Veto reuse of one upstream loan by another local identity.

    Ledger entries are evidence of a *conflict*, never of an entitlement.
    Malformed plugin or persisted rows cannot coerce identifiers or cause an
    exception that bypasses the admission guard. Unknown evidence fails closed.
    """
    if (type(provider_loan_id) is not str or not provider_loan_id.strip()
            or type(identity_id) is not str or not identity_id):
        return True
    loan_key = provider_loan_id.strip()
    for row in acquisitions:
        if type(row) is not dict:
            return True
        # An unrelated strategy (including historical None) cannot be used
        # to authorize ADEPT or to create a loan_auto owner conflict. Inspect
        # its metadata only if the strategy is the exact native loan_auto.
        strategy = row.get("strategy")
        if strategy is None:
            continue
        if type(strategy) is not str:
            return True
        if strategy != "loan_auto":
            continue
        metadata = row.get("metadata")
        if metadata is None:
            return True
        if type(metadata) is not dict:
            return True
        value = metadata.get("provider_loan_id")
        if value is None:
            continue
        if type(value) is not str:
            return True
        if value.strip() != loan_key:
            continue
        owner = row.get("identity_id")
        if type(owner) is not str or owner != identity_id:
            return True
    for row in entitlements:
        if type(row) is not dict:
            return True
        value = row.get("loan_id")
        if value is None:
            continue
        if type(value) is not str:
            return True
        if value.strip() != loan_key:
            continue
        owner = row.get("identity_id")
        if type(owner) is not str or owner != identity_id:
            return True
    return False
