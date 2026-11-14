"""P1 #6002 — passive, borrower-scoped *display* of digital loan availability.

The current Core stores positive loan statistics but no durable typed,
complete-and-fresh borrower/branch inventory, nor an attested remaining quota.
Consequently these records MUST remain unknown until a separate evidence
producer and durable freshness/revocation contract are actually delivered.
Nothing in this API grants a loan, changes permissions or reads a provider.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DigitalAvailabilityStatus = Literal["unknown", "estimated", "attested"]
DigitalAvailabilityReason = Literal[
    "inventory_unverified", "borrower_unverified", "account_disconnected",
    "provider_unverified", "account_disabled", "scope_ambiguous",
    "observation_stale", "subscription_unverified",
]


@dataclass(frozen=True, slots=True)
class DigitalLoanAvailability:
    """One exact identity/provider/credential/upstream borrower/digital branch.

    Internal scope coordinates are not user-facing text. `observed_loans`
    is an informational count of distinct positive, previously loaded active
    digital loans; it is *not* a complete inventory or a quota subtraction.
    Null means "not observed", NEVER zero.
    """
    identity_id: str
    provider_id: str
    credential_id: str = field(repr=False)
    borrower_id: str | None = field(default=None, repr=False)
    provider_label: str = ""
    connection_label: str = ""
    borrower_label: str = ""
    branch: str = "digital"
    observed_loans: int | None = None
    personal_ceiling: int | None = None
    remaining: int | None = None
    status: DigitalAvailabilityStatus = "unknown"
    reason: DigitalAvailabilityReason = "inventory_unverified"
    observed_at: str | None = None
    eligibility: Literal["not_verified"] = "not_verified"

    def __post_init__(self):
        # This first version is intentionally read-only unknown: the producer
        # cannot persist a proof of full, current inventory. Do not promote a
        # local ceiling or a cache observation into an estimated/attested N.
        if (self.status != "unknown" or self.remaining is not None
                or self.observed_at is not None
                or self.eligibility != "not_verified"
                or self.branch != "digital"):
            raise ValueError("digital_loan_availability_evidence_unavailable")
        if (type(self.identity_id) is not str or not self.identity_id
                or type(self.provider_id) is not str or not self.provider_id
                or type(self.credential_id) is not str or not self.credential_id
                or (self.borrower_id is not None and type(self.borrower_id) is not str)
                or self.reason not in DigitalAvailabilityReason.__args__):
            raise ValueError("digital_loan_availability_invalid_scope")
        for amount in (self.observed_loans, self.personal_ceiling):
            if amount is not None and (type(amount) is not int or not 0 <= amount <= 10000):
                raise ValueError("digital_loan_availability_invalid_count")
        if self.personal_ceiling is not None and self.personal_ceiling > 99:
            raise ValueError("digital_loan_availability_invalid_ceiling")


__all__ = ["DigitalLoanAvailability", "DigitalAvailabilityStatus", "DigitalAvailabilityReason"]
