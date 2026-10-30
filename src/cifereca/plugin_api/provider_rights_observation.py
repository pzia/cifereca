"""#3252: optional provider-neutral rights observation contract, not an action API.

A right reported by a provider, an exact available file-delivery offer,
a historical receipt, a catalogue notice and local bytes are different kinds
of evidence. A delivery offer does not require a declared active right. This transport envelope is only
a *claim* from the scoped producer. Core owns permission, persistence, consent,
artifact verification, sync epochs and idempotent transaction decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

RIGHTS_OBSERVATION_CONTRACT = "cifereca.rights.observation/1"

RightKind = Literal["purchase", "loan"]
RightState = Literal["active", "inactive", "unknown"]
RightEvidenceKind = Literal["provider_response", "historical_receipt", "legacy_unverified"]
DeliveryAvailability = Literal["available", "unavailable", "unknown"]
SnapshotStatus = Literal["success", "partial", "failed", "cancelled"]
Coverage = Literal["complete", "partial", "unknown"]

_MAX_ROWS = 10_000


def _token(value: object, *, empty: bool = False, limit: int = 256) -> bool:
    return (type(value) is str and len(value) <= limit
            and (empty or bool(value))
            and not any(c in value for c in ("\x00", "\r", "\n")))


def _timestamp(value: object) -> bool:
    if not _token(value, limit=40) or len(value) < 20:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and 2000 <= parsed.year <= 2100
    except (ValueError, OverflowError):
        return False


@dataclass(frozen=True, slots=True)
class ProviderRightsScope:
    """Exact provider/identity/credential/account key; empty primary account valid."""

    identity_id: str
    provider_id: str
    credential_id: str
    account_id: str

    def __post_init__(self) -> None:
        if not (all(_token(x) for x in (
                self.identity_id, self.provider_id, self.credential_id))
                and _token(self.account_id, empty=True)):
            raise ValueError("invalid_rights_observation_scope")


@dataclass(frozen=True, slots=True)
class ProviderRightObservation:
    """A positive *reported* right; no file/adoption or action is implied.

    The provider owns the exact source item and right identifiers. A row with
    'legacy_unverified' is never attested, even when state='active'.
    'available' describes an observed provider delivery *offer*, not downloaded
    bytes. The right state is descriptive and may be 'unknown' even when an
    exact, currently usable offer is known.
    """

    edition_id: str
    right_id: str
    kind: RightKind
    state: RightState = "unknown"
    evidence_kind: RightEvidenceKind = "legacy_unverified"
    evidence_ref: str = ""
    observed_at: str | None = None
    delivery: DeliveryAvailability = "unknown"
    delivery_offer_id: str = ""
    delivery_unavailable_reason: Literal["", "archived"] = ""

    def __post_init__(self) -> None:
        if not _token(self.edition_id) or not _token(self.right_id):
            raise ValueError("invalid_rights_observation_edition")
        if self.kind not in ("purchase", "loan") or type(self.kind) is not str:
            raise ValueError("invalid_rights_observation_kind")
        if self.state not in ("active", "inactive", "unknown") or type(self.state) is not str:
            raise ValueError("invalid_rights_observation_state")
        if (self.evidence_kind not in (
                "provider_response", "historical_receipt", "legacy_unverified")
                or type(self.evidence_kind) is not str):
            raise ValueError("invalid_rights_observation_evidence")
        if not _token(self.evidence_ref, empty=True):
            raise ValueError("invalid_rights_observation_evidence")
        if self.observed_at is not None and not _timestamp(self.observed_at):
            raise ValueError("invalid_rights_observation_timestamp")
        if self.evidence_kind != "legacy_unverified":
            if not self.evidence_ref or self.observed_at is None:
                raise ValueError("unproven_rights_observation")
        elif self.evidence_ref or self.observed_at is not None:
            # Legacy assertions have no provenance, not a forged receipt.
            raise ValueError("invalid_rights_observation_evidence")
        if self.delivery not in ("available", "unavailable", "unknown") or type(self.delivery) is not str:
            raise ValueError("invalid_rights_delivery")
        if not _token(self.delivery_offer_id, empty=True):
            raise ValueError("invalid_rights_delivery")
        if (self.delivery == "available") != bool(self.delivery_offer_id):
            raise ValueError("unproven_rights_delivery")
        if (type(self.delivery_unavailable_reason) is not str
                or self.delivery_unavailable_reason not in ("", "archived")
                or (self.delivery_unavailable_reason and (
                    self.delivery != "unavailable" or not self.provider_attested))):
            raise ValueError("invalid_rights_delivery_reason")
        # The provider's *delivery capacity* is independent from optional
        # purchase/loan-state metadata. An exact, fresh provider response may
        # offer a file even when the right state is unknown/inactive. Never
        # turn a historical/legacy assertion into a positive remote offer.
        if self.delivery == "available" and self.evidence_kind != "provider_response":
            raise ValueError("unproven_rights_delivery")

    @property
    def provider_attested(self) -> bool:
        """A referenced observation; still NOT an ownership adjudication."""
        return self.evidence_kind == "provider_response"

    @property
    def delivery_attested(self) -> bool:
        """Exact scoped provider offer, independent of optional right state."""
        return self.provider_attested and self.delivery == "available" and bool(self.delivery_offer_id)

    @property
    def authorizes_mutation(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ProviderRightsSnapshot:
    """Bounded atomic source claim, never sufficient to remove missing rights.

    'complete' describes only this branch and producer's claimed pagination.
    Absence of an edition in a snapshot is NOT proof of revoked ownership.
    A partial/failed/cancelled read cannot become a completed inventory.
    """

    scope: ProviderRightsScope
    rights: tuple[ProviderRightObservation, ...] = ()
    status: SnapshotStatus = "partial"
    coverage: Coverage = "unknown"
    pagination_complete: bool | None = None
    branch: str = ""
    observed_at: str | None = None
    snapshot_ref: str = ""
    resume_cursor: str = ""
    schema_version: str = RIGHTS_OBSERVATION_CONTRACT

    def __post_init__(self) -> None:
        if type(self.scope) is not ProviderRightsScope or self.schema_version != RIGHTS_OBSERVATION_CONTRACT:
            raise ValueError("invalid_rights_observation_contract")
        if (type(self.rights) is not tuple or len(self.rights) > _MAX_ROWS
                or any(type(row) is not ProviderRightObservation for row in self.rights)):
            raise ValueError("invalid_rights_observation_rows")
        if self.status not in ("success", "partial", "failed", "cancelled") or type(self.status) is not str:
            raise ValueError("invalid_rights_observation_status")
        if self.coverage not in ("complete", "partial", "unknown") or type(self.coverage) is not str:
            raise ValueError("invalid_rights_observation_coverage")
        if self.pagination_complete is not None and type(self.pagination_complete) is not bool:
            raise ValueError("invalid_rights_observation_pagination")
        if not all(_token(x, empty=True) for x in (self.branch, self.snapshot_ref, self.resume_cursor)):
            raise ValueError("invalid_rights_observation_reference")
        if self.observed_at is not None and not _timestamp(self.observed_at):
            raise ValueError("invalid_rights_observation_timestamp")
        if self.status in ("failed", "cancelled") and (
                self.rights or self.coverage != "unknown" or self.pagination_complete is True):
            raise ValueError("invalid_rights_observation_failed_rows")
        if self.coverage == "complete" and not (
                self.status == "success" and self.pagination_complete is True
                and self.observed_at is not None and self.snapshot_ref and self.branch):
            raise ValueError("unproven_rights_observation_completeness")
        if self.coverage == "complete" and self.resume_cursor:
            raise ValueError("unproven_rights_observation_completeness")
        if self.status != "partial" and self.resume_cursor:
            raise ValueError("invalid_rights_observation_resume")
        if self.rights and not self.branch:
            raise ValueError("invalid_rights_observation_branch")
        seen = set()
        for row in self.rights:
            key = (row.edition_id, row.right_id, row.kind)
            if key in seen:
                raise ValueError("ambiguous_rights_observation_rows")
            seen.add(key)

    def exact(self, edition_id: str, right_id: str, kind: RightKind) -> ProviderRightObservation | None:
        """No fuzzy title/ISBN matches; never projects another account's rows."""
        return next((row for row in self.rights if
                     (row.edition_id, row.right_id, row.kind) ==
                     (edition_id, right_id, kind)), None)

    @property
    def claims_complete_branch(self) -> bool:
        return self.coverage == "complete" and self.status == "success"

    @property
    def authorizes_mutation(self) -> bool:
        return False

    @property
    def authorizes_negative_reconciliation(self) -> bool:
        return False


__all__ = [
    "RIGHTS_OBSERVATION_CONTRACT", "ProviderRightsScope",
    "ProviderRightObservation", "ProviderRightsSnapshot",
]
