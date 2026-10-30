"""#3719: versioned provider-neutral downloadable-resource transport contract.

A SourceReference identifies an authenticated resource in an exact account and
branch. DownloadCapability describes an observed *offer*; OptionalRightMetadata
is descriptive only. Neither this contract nor a provider candidate authorizes
network access, filesystem placement, ADEPT, or SQLite writes. The Core alone
checks source-wide consent, live identity/epoch and the final content bytes.

The v1 rights envelope remains supported through explicit positive-only adapters.
This module does not introduce a second synchronization executor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from .provider_rights_observation import (
    ProviderRightObservation, ProviderRightsScope, ProviderRightsSnapshot,
)

DOWNLOADABLE_RESOURCE_CONTRACT = "cifereca.downloadable-resource/2"

ResourceKind = Literal["purchase", "loan"]
CapabilityState = Literal["available", "unavailable", "unknown"]
RightState = Literal["active", "inactive", "unknown"]
SnapshotStatus = Literal["success", "partial", "failed", "cancelled"]
Coverage = Literal["complete", "partial", "unknown"]
_MAX_ROWS = 10_000


def _token(value: object, *, empty: bool = False) -> bool:
    return (type(value) is str and len(value) <= 256
            and (empty or bool(value)) and
            not any(c in value for c in "\x00\r\n"))


def _timestamp(value: object) -> bool:
    if not _token(value) or len(value) < 20:
        return False
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.tzinfo is not None and 2000 <= date.year <= 2100
    except (ValueError, OverflowError):
        return False


@dataclass(frozen=True, slots=True)
class SourceReference:
    """A: exact resource coordinates, *not* a legal entitlement record.

    resource_id is the provider's stable exact reference for this acquisition;
    it is required even when legal-right metadata does not exist. It maps to
    the legacy right_id transport slot without inventing a right state.
    """

    scope: ProviderRightsScope
    branch: str
    edition_id: str
    resource_id: str
    kind: ResourceKind

    def __post_init__(self) -> None:
        if (type(self.scope) is not ProviderRightsScope
                or not all(_token(x) for x in (
                    self.branch, self.edition_id, self.resource_id))
                or type(self.kind) is not str
                or self.kind not in ("purchase", "loan")):
            raise ValueError("invalid_downloadable_source_reference")


@dataclass(frozen=True, slots=True)
class DownloadCapability:
    """The producer's exact availability observation, not consent or a GET."""

    state: CapabilityState
    offer_id: str = ""
    unavailable_reason: Literal["", "archived"] = ""

    def __post_init__(self) -> None:
        if (type(self.state) is not str or
                self.state not in ("available", "unavailable", "unknown")
                or not _token(self.offer_id, empty=True)
                or (self.state == "available") != bool(self.offer_id)
                or type(self.unavailable_reason) is not str
                or self.unavailable_reason not in ("", "archived")
                or (self.unavailable_reason and self.state != "unavailable")):
            raise ValueError("invalid_download_capability")

    @property
    def downloadable(self) -> bool:
        """Positive *capacity* only; Core admission must still check consent."""
        return self.state == "available"


@dataclass(frozen=True, slots=True)
class OptionalRightMetadata:
    """Descriptive legal/loan state. Never an independent GET veto or grant."""

    state: RightState = "unknown"
    right_id: str = ""
    expires_at: str = ""

    def __post_init__(self) -> None:
        if (type(self.state) is not str
                or self.state not in ("active", "inactive", "unknown")
                or not _token(self.right_id, empty=True)
                or not _token(self.expires_at, empty=True)
                or (self.expires_at and not _timestamp(self.expires_at))):
            raise ValueError("invalid_optional_right_metadata")


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    """One attested provider resource; metadata rights may be absent."""

    source: SourceReference
    capability: DownloadCapability
    evidence_ref: str
    observed_at: str
    right: OptionalRightMetadata | None = None

    def __post_init__(self) -> None:
        if (type(self.source) is not SourceReference
                or type(self.capability) is not DownloadCapability
                or not _token(self.evidence_ref)
                or not _timestamp(self.observed_at)
                or (self.right is not None and type(self.right) is not OptionalRightMetadata)):
            raise ValueError("invalid_downloadable_observation")

    @property
    def can_propose_delivery(self) -> bool:
        """A genuine offer, not an execution authorization."""
        return self.capability.downloadable

    @property
    def authorizes_acquisition(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """A bounded source observation. Missing rows never revoke local sources."""

    scope: ProviderRightsScope
    branch: str
    resources: tuple[ResourceObservation, ...]
    status: SnapshotStatus
    coverage: Coverage = "unknown"
    pagination_complete: bool | None = None
    observed_at: str | None = None
    snapshot_ref: str = ""
    resume_cursor: str = ""
    schema_version: str = DOWNLOADABLE_RESOURCE_CONTRACT

    def __post_init__(self) -> None:
        if (type(self.scope) is not ProviderRightsScope
                or self.schema_version != DOWNLOADABLE_RESOURCE_CONTRACT
                or not _token(self.branch)
                or type(self.resources) is not tuple
                or len(self.resources) > _MAX_ROWS
                or any(type(x) is not ResourceObservation for x in self.resources)
                or type(self.status) is not str
                or self.status not in ("success", "partial", "failed", "cancelled")
                or type(self.coverage) is not str
                or self.coverage not in ("complete", "partial", "unknown")
                or (self.pagination_complete is not None
                    and type(self.pagination_complete) is not bool)
                or (self.observed_at is not None and not _timestamp(self.observed_at))
                or not _token(self.snapshot_ref, empty=True)
                or not _token(self.resume_cursor, empty=True)):
            raise ValueError("invalid_downloadable_snapshot")
        if self.status in ("failed", "cancelled") and (
                self.resources or self.coverage != "unknown"
                or self.pagination_complete is True):
            raise ValueError("invalid_failed_downloadable_snapshot")
        if self.coverage == "complete" and not (
                self.status == "success" and self.pagination_complete is True
                and self.observed_at is not None and self.snapshot_ref
                and not self.resume_cursor):
            raise ValueError("unproven_downloadable_completeness")
        if self.status != "partial" and self.resume_cursor:
            raise ValueError("invalid_downloadable_resume")
        seen: set[tuple[str, str, str]] = set()
        for observation in self.resources:
            source = observation.source
            if source.scope != self.scope or source.branch != self.branch:
                raise ValueError("downloadable_resource_scope_mismatch")
            key = (source.edition_id, source.resource_id, source.kind)
            if key in seen:
                raise ValueError("ambiguous_downloadable_resources")
            seen.add(key)

    @property
    def authorizes_acquisition(self) -> bool:
        return False

    @property
    def authorizes_negative_reconciliation(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class DeliveryCandidate:
    """Plugin result confined to Core staging, NOT completed delivery proof.

    The Core validates the path against its newly created staging directory,
    verifies format/bytes/SHA and mints its own independent delivery receipt.
    An opaque provider hint may be present but can never assert a Placement.
    """

    source: SourceReference
    selected_offer_id: str
    path: Path
    provider_hint: str = ""

    def __post_init__(self) -> None:
        if (type(self.source) is not SourceReference
                or not _token(self.selected_offer_id)
                or not isinstance(self.path, Path)
                or not _token(self.provider_hint, empty=True)):
            raise ValueError("invalid_delivery_candidate")

    @property
    def authorizes_publication(self) -> bool:
        return False


def from_legacy_rights_snapshot(snapshot: ProviderRightsSnapshot) -> ResourceSnapshot:
    """Lossless for attested resources. Unverified v1 rows are not positive A.

    In particular state='unknown' or 'inactive' does not hide a positive
    exact delivery offer. Unattested legacy rows are dropped, and branch
    completeness is conservatively downgraded if any were present.
    """
    if type(snapshot) is not ProviderRightsSnapshot:
        raise ValueError("invalid_legacy_downloadable_snapshot")
    resources = []
    for item in snapshot.rights:
        if not item.provider_attested:
            continue
        source = SourceReference(
            snapshot.scope, snapshot.branch, item.edition_id, item.right_id,
            item.kind,
        )
        resources.append(ResourceObservation(
            source=source,
            capability=DownloadCapability(
                item.delivery, item.delivery_offer_id,
                item.delivery_unavailable_reason,
            ),
            evidence_ref=item.evidence_ref,
            observed_at=item.observed_at,
            right=OptionalRightMetadata(state=item.state),
        ))
    full_attestation = len(resources) == len(snapshot.rights)
    return ResourceSnapshot(
        scope=snapshot.scope,
        branch=snapshot.branch or "unobserved",
        resources=tuple(resources),
        status=snapshot.status,
        coverage=snapshot.coverage if full_attestation else "unknown",
        pagination_complete=(snapshot.pagination_complete if full_attestation
                             else False),
        observed_at=snapshot.observed_at,
        snapshot_ref=snapshot.snapshot_ref,
        resume_cursor=snapshot.resume_cursor,
    )


def as_legacy_rights_snapshot(snapshot: ResourceSnapshot) -> ProviderRightsSnapshot:
    """Explicit adapter for existing Core executor; never creates legal rights.

    The v1 right_id slot is the *resource identifier*, not a synthetic grant.
    Core consent, source binding and positive delivery checks remain mandatory.
    """
    if type(snapshot) is not ResourceSnapshot:
        raise ValueError("invalid_downloadable_snapshot_adapter")
    return ProviderRightsSnapshot(
        scope=snapshot.scope, branch=snapshot.branch,
        rights=tuple(
            ProviderRightObservation(
                edition_id=item.source.edition_id,
                right_id=item.source.resource_id,
                kind=item.source.kind,
                state=item.right.state if item.right else "unknown",
                evidence_kind="provider_response",
                evidence_ref=item.evidence_ref,
                observed_at=item.observed_at,
                delivery=item.capability.state,
                delivery_offer_id=item.capability.offer_id,
                delivery_unavailable_reason=item.capability.unavailable_reason,
            ) for item in snapshot.resources
        ),
        status=snapshot.status, coverage=snapshot.coverage,
        pagination_complete=snapshot.pagination_complete,
        observed_at=snapshot.observed_at, snapshot_ref=snapshot.snapshot_ref,
        resume_cursor=snapshot.resume_cursor,
    )


__all__ = [
    "DOWNLOADABLE_RESOURCE_CONTRACT", "SourceReference",
    "DownloadCapability", "OptionalRightMetadata", "ResourceObservation",
    "ResourceSnapshot", "DeliveryCandidate", "from_legacy_rights_snapshot",
    "as_legacy_rights_snapshot",
]
