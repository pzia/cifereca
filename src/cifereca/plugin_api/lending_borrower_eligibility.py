"""Read-only, borrower-exact loan eligibility (#3120).

A provider capability (or catalogue CanLoan flag) never authorizes a borrower.
Only separately attested facts for the *same* provider, session credential,
borrower, branch and edition may explain why a new loan is blocked. Results
are advisory: Core must still revalidate live permission, consent and scope
before *any* mutating provider request.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cifereca.models import Account, SearchResult
from .lending_capabilities import LendingOperationFact


BorrowerEligibilityStatus = Literal["unavailable", "unverified", "blocked", "eligible"]


@dataclass(frozen=True, slots=True)
class BorrowerOperationEvidence:
    """Already-observed, operation-specific facts; NOT an authorization token.

    Producers must independently prove each fact against the exact borrower
    and branch (e.g. DILICOM, not a generic SYRACUSE summary). Missing or
    ambiguous upstream signals MUST be "unknown", never assumed positive.
    No network call, quota calculation or capability probing is done here.
    """
    identity_id: str
    provider_id: str
    credential_id: str
    borrower_id: str
    docbase: str
    record_id: str
    source_collection: str | None = None
    operation: str = "loan.borrow"
    account_state: str = "unknown"
    quota_state: str = "unknown"
    item_offer: str = "unknown"
    permission: str = "unknown"


@dataclass(frozen=True, slots=True)
class BorrowerEligibilityFact:
    status: BorrowerEligibilityStatus
    reason_code: str
    capability_supported: bool
    account_ready: bool

    @property
    def blocked(self) -> bool:
        """Attested negative, distinct from absence of positive proof."""
        return self.status == "blocked"


def _bounded(value: object, *, allow_empty: bool = False) -> bool:
    return (type(value) is str and len(value) <= 1024
            and "\x00" not in value and (allow_empty or bool(value)))


def assess_borrower_eligibility(
    *, capability: LendingOperationFact | None,
    account: Account, notice: SearchResult,
    evidence: BorrowerOperationEvidence | None = None,
) -> BorrowerEligibilityFact:
    """Classify borrower/branch permission without contacting any provider.

    `eligible` means *only* that the caller supplied all four independent
    positive facts for the matching operation/borrower/edition. The returned
    object is NOT accepted by Core as proof of permission, provider effect
    or user confirmation. Provider-origin evidence freshness remains the
    caller's responsibility.
    """
    def result(status: BorrowerEligibilityStatus, code: str, *,
               capability_ok: bool = False, account_ok: bool = False):
        return BorrowerEligibilityFact(status, code, capability_ok, account_ok)

    if type(capability) is not LendingOperationFact:
        return result("unverified", "capability_unverified")
    if (capability.operation != "loan.borrow"
            or capability.availability != "supported"
            or capability.manifest_declared is not True
            or capability.descriptor_declared is not True
            or capability.method_implemented is not True):
        return result("unavailable", "capability_unavailable")
    if capability.session_ready is not True:
        return result("unavailable", "session_unavailable")
    if type(account) is not Account or type(notice) is not SearchResult:
        return result("unverified", "scope_unverified", capability_ok=True)
    if (not all(_bounded(v) for v in (
            account.identity_id, account.provider_id, account.credential_id,
            notice.record_id, notice.docbase, notice.source_provider,
            notice.source_credential))
            or not _bounded(account.unique_id, allow_empty=True)
            or (notice.source_collection is not None
                and not _bounded(notice.source_collection))
            or notice.source_provider != account.provider_id
            or notice.source_credential != account.credential_id
            or notice.medium != "digital"):
        return result("unverified", "scope_unverified", capability_ok=True)

    if type(evidence) is not BorrowerOperationEvidence:
        return result("unverified", "evidence_missing", capability_ok=True,
                      account_ok=True)
    if (evidence.operation != "loan.borrow"
            or not all(_bounded(v) for v in (
                evidence.identity_id, evidence.provider_id,
                evidence.credential_id, evidence.docbase, evidence.record_id))
            or not _bounded(evidence.borrower_id, allow_empty=True)
            or (evidence.source_collection is not None
                and not _bounded(evidence.source_collection))
            or (evidence.identity_id, evidence.provider_id,
                evidence.credential_id, evidence.borrower_id,
                evidence.docbase, evidence.record_id,
                evidence.source_collection) != (
                account.identity_id, account.provider_id,
                account.credential_id, account.unique_id,
                notice.docbase, notice.record_id,
                notice.source_collection)):
        return result("unverified", "evidence_scope_mismatch", capability_ok=True,
                      account_ok=True)

    if (type(evidence.account_state) is not str
            or evidence.account_state not in {
                "active", "renewal_pending", "inactive", "unknown"}
            or type(evidence.quota_state) is not str
            or evidence.quota_state not in {"available", "exhausted", "unknown"}
            or type(evidence.item_offer) is not str
            or evidence.item_offer not in {"available", "unavailable", "unknown"}
            or type(evidence.permission) is not str
            or evidence.permission not in {"allowed", "denied", "unknown"}):
        return result("unverified", "evidence_invalid", capability_ok=True,
                      account_ok=True)

    # A proven denial wins over an unrelated unknown, but never over a scope
    # mismatch: a different card's exhausted quota cannot block this borrower.
    if evidence.account_state == "renewal_pending":
        return result("blocked", "subscription_renewal_pending",
                      capability_ok=True, account_ok=True)
    if evidence.account_state == "inactive":
        return result("blocked", "subscription_inactive",
                      capability_ok=True, account_ok=True)
    if evidence.quota_state == "exhausted":
        return result("blocked", "borrower_quota_exhausted",
                      capability_ok=True, account_ok=True)
    if evidence.permission == "denied":
        return result("blocked", "borrower_permission_denied",
                      capability_ok=True, account_ok=True)
    if evidence.item_offer == "unavailable":
        return result("blocked", "item_offer_unavailable",
                      capability_ok=True, account_ok=True)

    if evidence.account_state == "unknown":
        return result("unverified", "subscription_unverified",
                      capability_ok=True, account_ok=True)
    if evidence.quota_state == "unknown":
        return result("unverified", "quota_unverified",
                      capability_ok=True, account_ok=True)
    if evidence.permission == "unknown":
        return result("unverified", "permission_unverified",
                      capability_ok=True, account_ok=True)
    if evidence.item_offer == "unknown":
        return result("unverified", "item_offer_unverified",
                      capability_ok=True, account_ok=True)
    return result("eligible", "borrower_eligible_unconfirmed",
                  capability_ok=True, account_ok=True)


__all__ = [
    "BorrowerEligibilityStatus", "BorrowerOperationEvidence",
    "BorrowerEligibilityFact", "assess_borrower_eligibility",
]
