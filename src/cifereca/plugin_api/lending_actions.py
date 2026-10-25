"""Additive, non-dispatching lending action contract (Core P1 #3121).

Static availability, an upstream Loan, a provider method and a cached EPUB URL
are NOT a user's consent, a loan entitlement or proof of a remote mutation.
Core must own admission, the durable at-most-once claim, the exact live scope
and independent provider verification before taking any action.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cifereca.models import Account, Loan, SearchResult
from .lending_capabilities import LendingOperationFact

LENDING_ACTION_CONTRACT = "cifereca.loan.action/1"
LendingActionOrigin = Literal["manual", "opening", "scheduled"]
LendingActionStatus = Literal[
    "unsupported", "blocked", "cancelled", "failed", "submitted",
    "confirmed", "verification_unavailable",
]
LendingActionEvidence = Literal["none", "provider_accepted", "provider_verified"]
_SUPPORTED_ACTIONS = frozenset({
    "loan.borrow", "loan.renew", "loan.return", "loan.download_token",
})
_REASON_CODES = frozenset({
    "not_declared", "capability_unavailable", "origin_not_permitted",
    "confirmation_required", "permission_unverified", "scope_unverified",
    "loan_not_active", "loan_unverified", "operation_unavailable",
    "cancelled", "provider_refused", "provider_unavailable",
    "provider_response_unverified", "provider_confirmation_unavailable",
    "provider_confirmed", "provider_submitted", "admission_ready", "unknown",
})


@dataclass(frozen=True, slots=True)
class LendingActionOriginPolicy:
    """Core-owned origin policy; default is no automation of any action.

    The historical Paris opening/ADEPT implementation remains a *separate*
    guarded path. A plugin must not manufacture or persist one of these flags
    as a permission. Enabling an origin does not remove the explicit consent
    and per-loan permission checks below.
    """
    manual: bool = True
    opening: bool = False
    scheduled: bool = False

    def __post_init__(self):
        if any(type(v) is not bool for v in (self.manual, self.opening, self.scheduled)):
            raise ValueError("invalid_loan_action_origin_policy")


@dataclass(frozen=True, slots=True)
class LendingActionAdmission:
    operation: str
    origin: LendingActionOrigin
    status: Literal["admitted", "unsupported", "blocked", "cancelled"]
    reason_code: str

    @property
    def admitted(self) -> bool:
        """Advisory decision only, never authority to run a provider method."""
        return self.status == "admitted"


def _exact_target(operation: str, target: object, account: Account) -> bool:
    if type(account) is not Account:
        return False
    if (type(account.identity_id) is not str or not account.identity_id
            or type(account.provider_id) is not str or not account.provider_id
            or type(account.credential_id) is not str or not account.credential_id
            or type(account.unique_id) is not str):
        return False
    if operation == "loan.borrow":
        # Borrowing has not yet produced a Loan. An exact catalogue notice is
        # necessary but is NEVER proof of an available upstream borrow offer.
        return bool(
            type(target) is SearchResult
            and type(target.id) is str and target.id
            and type(target.record_id) is str and target.record_id
            and target.source_provider == account.provider_id
            and target.source_credential == account.credential_id
        )
    if type(target) is not Loan:
        return False
    borrowers = {account.unique_id}
    if (account.provider_id == "paris" and account.unique_id == ""
            and account.is_primary is True):
        borrowers.add("primary")
    return bool(
        type(target.id) is str and target.id
        and type(target.record_id) is str and target.record_id
        and target.source_provider == account.provider_id
        and target.source_credential == account.credential_id
        and target.source_account_id in borrowers
        and target.identity_id == account.identity_id
        and target.medium in {"digital", "physical"}
    )


def assess_lending_action(
    operation: str, *, fact: LendingOperationFact | None,
    account: Account, target: Loan | SearchResult,
    origin: LendingActionOrigin = "manual",
    confirmed: bool = False, permission_attested: bool = False,
    cancelled: bool = False,
    policy: LendingActionOriginPolicy = LendingActionOriginPolicy(),
) -> LendingActionAdmission:
    """No I/O. Fail closed on capability, origin, consent and exact subject.

    The boolean 'permission_attested' MUST come from a separate fresh
    Core-authoritative permission/entitlement check, not Loan.renewable,
    can_return, a button, a token or a SearchResult. This function cannot
    grant permissions or run any action. Consumers must revalidate the live
    session/epoch immediately before AND after provider I/O.
    """
    key = operation if type(operation) is str and operation in _SUPPORTED_ACTIONS else ""
    selected_origin = origin if type(origin) is str and origin in (
        "manual", "opening", "scheduled",
    ) else "manual"

    def decision(status: str, reason: str) -> LendingActionAdmission:
        return LendingActionAdmission(key or "unknown", selected_origin, status, reason)

    if not key:
        return decision("unsupported", "operation_unavailable")
    if type(origin) is not str or origin not in ("manual", "opening", "scheduled"):
        return decision("blocked", "origin_not_permitted")
    if cancelled is True:
        return decision("cancelled", "cancelled")
    if type(policy) is not LendingActionOriginPolicy:
        return decision("blocked", "origin_not_permitted")
    if not getattr(policy, origin):
        return decision("blocked", "origin_not_permitted")
    # Never open or schedule remote borrow/return/renew. An opt-in token read
    # still needs a persisted Core policy, explicit consent and exact loan.
    if origin != "manual" and key != "loan.download_token":
        return decision("blocked", "origin_not_permitted")
    if confirmed is not True:
        return decision("blocked", "confirmation_required")
    if (type(fact) is not LendingOperationFact or fact.operation != key
            or fact.availability != "supported"
            or fact.manifest_declared is not True
            or fact.descriptor_declared is not True
            or fact.method_implemented is not True
            or fact.session_ready is not True
            or fact.explicit_action_required is not True):
        return decision("unsupported", "not_declared")
    if not _exact_target(key, target, account):
        return decision("blocked", "scope_unverified")
    if key != "loan.borrow" and target.state != "active":
        return decision("blocked", "loan_not_active")
    if key in {"loan.return", "loan.download_token"} and target.medium != "digital":
        return decision("blocked", "loan_unverified")
    if permission_attested is not True:
        return decision("blocked", "permission_unverified")
    return decision("admitted", "admission_ready")


@dataclass(frozen=True, slots=True)
class LendingActionOutcome:
    """Typed *claim* from an action boundary, not a rights write permit.

    HTTP 2xx or an uploaded/staged local ACSM cannot become 'confirmed'
    unless a separate provider-verification step actually attests the effect.
    A caller may construct this record, but persistence must independently
    verify durable scope and origin; the object is never a proof token.
    """
    operation: str
    origin: LendingActionOrigin
    status: LendingActionStatus
    evidence: LendingActionEvidence = "none"
    reason_code: str = "unknown"
    contract: str = LENDING_ACTION_CONTRACT

    def __post_init__(self):
        if (self.contract != LENDING_ACTION_CONTRACT
                or type(self.operation) is not str
                or self.operation not in _SUPPORTED_ACTIONS
                or self.origin not in ("manual", "opening", "scheduled")
                or self.status not in (
                    "unsupported", "blocked", "cancelled", "failed",
                    "submitted", "confirmed", "verification_unavailable",
                )
                or self.evidence not in (
                    "none", "provider_accepted", "provider_verified",
                )
                or self.reason_code not in _REASON_CODES):
            raise ValueError("invalid_loan_action_outcome")
        if (self.status == "confirmed" and
                (self.evidence != "provider_verified"
                 or self.operation == "loan.download_token")):
            raise ValueError("unverified_loan_action_confirmation")
        if (self.status == "submitted" and self.evidence != "provider_accepted"):
            raise ValueError("unverified_loan_action_submission")
        if (self.status in {"unsupported", "blocked", "cancelled", "failed"}
                and self.evidence != "none"):
            raise ValueError("invalid_loan_action_evidence")


__all__ = [
    "LENDING_ACTION_CONTRACT", "LendingActionOrigin",
    "LendingActionOriginPolicy", "LendingActionAdmission",
    "assess_lending_action", "LendingActionStatus", "LendingActionOutcome",
]
