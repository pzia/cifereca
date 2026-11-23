"""P1 #6002: pure priority policy for *proven* digital-loan capacity facts.

This evaluator does not read a provider, a session, a cache or SQLite. The
current Home adapter deliberately supplies no positive receipts; only a
future durable, scope-bound evidence producer may invoke this function.
An advisory limit and positive cached loans alone never prove availability.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal


def _safe_identifier(value: object, *, optional: bool = False) -> bool:
    """Exact private scope identifiers cannot contain control characters."""
    return (type(value) is str and (optional or bool(value))
            and len(value) <= 1024
            and all(ord(char) >= 32 and ord(char) != 127 for char in value))


@dataclass(frozen=True, slots=True)
class DigitalCapacityScope:
    identity_id: str
    provider_id: str
    credential_id: str
    account_id: str
    borrower_id: str
    branch: Literal["digital"] = "digital"

    def __post_init__(self) -> None:
        if (type(self.branch) is not str or self.branch != "digital"
                or any(not _safe_identifier(value, optional=name in ("account_id", "borrower_id"))
                       for name, value in (
                           ("identity_id", self.identity_id),
                           ("provider_id", self.provider_id),
                           ("credential_id", self.credential_id),
                           ("account_id", self.account_id),
                           ("borrower_id", self.borrower_id),
                       ))):
            raise ValueError("invalid_digital_capacity_scope")


@dataclass(frozen=True, slots=True)
class ProviderCapacityEvidence:
    """Provider-origin value: remaining directly OR a total ceiling.

    The producer must verify authenticity and provenance independently;
    construction here is not evidence of a real provider observation.
    """
    scope: DigitalCapacityScope
    kind: Literal["remaining", "total"]
    value: int
    observed_at: datetime
    epoch: int

    def __post_init__(self) -> None:
        if (type(self.scope) is not DigitalCapacityScope
                or type(self.kind) is not str or self.kind not in ("remaining", "total")
                or type(self.value) is not int or not 0 <= self.value <= 10000
                or not _valid_time(self.observed_at)
                or type(self.epoch) is not int or self.epoch < 0):
            raise ValueError("invalid_provider_capacity_evidence")


@dataclass(frozen=True, slots=True)
class DigitalInventoryReceipt:
    """One exact *digital* scope, with observed pagination and persisted epoch.

    The upstream producer must establish complete coverage and durable commit;
    an in-memory loan cache is NOT a valid receipt. The records are already
    filtered to active digital loans of this borrower by that producer.
    """
    scope: DigitalCapacityScope
    active_loan_ids: tuple[str, ...]
    observed_at: datetime
    epoch: int
    status: Literal["success", "partial", "failed", "cancelled"]
    pagination_complete: bool
    durably_committed: bool

    def __post_init__(self) -> None:
        if (type(self.scope) is not DigitalCapacityScope
                or type(self.active_loan_ids) is not tuple
                or len(self.active_loan_ids) > 10000
                or any(not _safe_identifier(value)
                       for value in self.active_loan_ids)
                or not _valid_time(self.observed_at)
                or type(self.epoch) is not int or self.epoch < 0
                or type(self.status) is not str
                or self.status not in ("success", "partial", "failed", "cancelled")
                or type(self.pagination_complete) is not bool
                or type(self.durably_committed) is not bool):
            raise ValueError("invalid_digital_inventory_receipt")


@dataclass(frozen=True, slots=True)
class DigitalRemainingDecision:
    status: Literal["provider_direct", "provider_calculated", "manual_estimate", "unknown"]
    remaining: int | None
    reason: Literal["provider_remaining", "provider_total", "personal_ceiling",
                    "inventory_unverified", "evidence_unavailable"]
    eligibility: Literal["not_verified"] = "not_verified"


def _valid_time(value: object) -> bool:
    return (type(value) is datetime and value.tzinfo is not None
            and value.utcoffset() is not None)


def _current(observed: datetime, *, now: datetime, max_age: timedelta) -> bool:
    age = now - observed
    return timedelta(0) <= age <= max_age



def _revalidate_fact(record: ProviderCapacityEvidence | DigitalInventoryReceipt) -> bool:
    """Recheck a frozen DTO at consumption, including its mutable-by-escape scope.

    Dataclass frozen/slots only blocks ordinary assignment: upstream Python
    adapters may still mutate fields with object.__setattr__. A stale valid
    constructor verdict must not turn a boolean, list or naïve timestamp into
    a displayed numeric capacity, nor crash the read-only Home projection.
    This is structural validation, NOT provider authenticity or durable proof.
    """
    try:
        record.__post_init__()
        record.scope.__post_init__()
    except (ValueError, TypeError, AttributeError, OverflowError):
        return False
    return True


def evaluate_digital_remaining(
    scope: DigitalCapacityScope, *, now: datetime, epoch: int,
    max_age: timedelta, provider_remaining: ProviderCapacityEvidence | None = None,
    provider_total: ProviderCapacityEvidence | None = None,
    manual_total: int | None = None,
    inventory: DigitalInventoryReceipt | None = None,
) -> DigitalRemainingDecision:
    """Direct provider remaining > provider total minus active > manual fallback.

    An exact, fresh, durable complete inventory is required for either
    subtraction; direct remaining is independent from inventory coverage.
    Every input is read-only. No result grants borrow eligibility or a POST.
    """
    if (type(scope) is not DigitalCapacityScope or not _valid_time(now)
            or type(epoch) is not int or epoch < 0
            or type(max_age) is not timedelta
            or not timedelta(0) < max_age <= timedelta(days=14)):
        raise ValueError("invalid_capacity_evaluation_context")
    try:
        scope.__post_init__()
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError("invalid_capacity_evaluation_context") from None
    if (manual_total is not None
            and (type(manual_total) is not int or not 0 <= manual_total <= 99)):
        raise ValueError("invalid_manual_loan_limit")
    for record in (provider_remaining, provider_total):
        if record is not None and type(record) is not ProviderCapacityEvidence:
            raise ValueError("invalid_capacity_evidence_type")
    if inventory is not None and type(inventory) is not DigitalInventoryReceipt:
        raise ValueError("invalid_inventory_evidence_type")

    def admissible(record: ProviderCapacityEvidence | None, kind: str) -> bool:
        return bool(record is not None and _revalidate_fact(record)
                    and record.scope == scope
                    and record.kind == kind and record.epoch == epoch
                    and _current(record.observed_at, now=now, max_age=max_age))

    if admissible(provider_remaining, "remaining"):
        return DigitalRemainingDecision("provider_direct", provider_remaining.value,
                                        "provider_remaining")
    receipt_good = (
        inventory is not None and _revalidate_fact(inventory)
        and inventory.scope == scope
        and inventory.epoch == epoch and inventory.status == "success"
        and inventory.pagination_complete is True
        and inventory.durably_committed is True
        and _current(inventory.observed_at, now=now, max_age=max_age)
    )
    if receipt_good:
        active = len(set(inventory.active_loan_ids))
        if admissible(provider_total, "total"):
            return DigitalRemainingDecision("provider_calculated",
                                            max(0, provider_total.value - active),
                                            "provider_total")
        if manual_total is not None:
            return DigitalRemainingDecision("manual_estimate",
                                            max(0, manual_total - active),
                                            "personal_ceiling")
    return DigitalRemainingDecision(
        "unknown", None,
        "inventory_unverified" if (
            manual_total is not None or provider_total is not None
        ) else "evidence_unavailable",
    )


__all__ = [
    "DigitalCapacityScope", "ProviderCapacityEvidence",
    "DigitalInventoryReceipt", "DigitalRemainingDecision",
    "evaluate_digital_remaining",
]
