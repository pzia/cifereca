"""#3171: provider-neutral, read-only A -> B convergence decision.

Only Core may construct placement and origin evidence from its verified store/scan.
Neither a plugin observation, a matching SHA/title, nor this plan authorizes I/O.
The executor must revalidate scope, consent, evidence and file bytes under its lock.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cifereca.plugin_api.provider_rights_observation import (
    ProviderRightsScope, ProviderRightsSnapshot,
)

DecisionState = Literal[
    "present_attested", "needs_reconciliation", "missing_to_download",
    "pending", "inactive",
]
OriginKind = Literal["provider", "manual", "transfer", "cifereca", "adept", "derived"]


def _id(value: object, *, empty: bool = False) -> bool:
    return (type(value) is str and len(value) <= 1024
            and (empty or bool(value))
            and not any(ch in value for ch in "\x00\r\n"))


@dataclass(frozen=True, slots=True)
class SourceKey:
    """A: one exact source reference; account, edition and right are distinct."""
    scope: ProviderRightsScope
    edition_id: str
    right_id: str
    kind: Literal["purchase", "loan"]

    def __post_init__(self) -> None:
        if (type(self.scope) is not ProviderRightsScope
                or not _id(self.edition_id) or not _id(self.right_id)
                or self.kind not in ("purchase", "loan")):
            raise ValueError("invalid_source_reference")


@dataclass(frozen=True, slots=True)
class PlacementEvidence:
    """B: Core's last verification result, scoped to a physical profile copy."""
    identity_id: str
    placement_id: str
    sha256: str
    size: int
    verified: bool

    def __post_init__(self) -> None:
        if (not _id(self.identity_id) or not _id(self.placement_id)
                or type(self.sha256) is not str or len(self.sha256) != 64
                or any(ch not in "0123456789abcdef" for ch in self.sha256)
                or type(self.size) is not int or self.size < 0
                or type(self.verified) is not bool):
            raise ValueError("invalid_placement_evidence")


@dataclass(frozen=True, slots=True)
class OriginEvidence:
    """Historical origin is NOT a right or a restoration instruction.

    Only a Core-attested 'provider' origin for the exact A key may satisfy A.
    ADEPT, transfer, internal and derived origins remain distinct history.
    """
    kind: OriginKind
    artifact_sha256: str
    source: SourceKey | None = None
    attested: bool = False

    def __post_init__(self) -> None:
        if (self.kind not in ("provider", "manual", "transfer", "cifereca", "adept", "derived")
                or type(self.kind) is not str
                or type(self.artifact_sha256) is not str
                or len(self.artifact_sha256) != 64
                or any(ch not in "0123456789abcdef" for ch in self.artifact_sha256)
                or (self.source is not None and type(self.source) is not SourceKey)
                or type(self.attested) is not bool
                or (self.kind == "provider" and self.source is None)):
            raise ValueError("invalid_origin_evidence")


@dataclass(frozen=True, slots=True)
class ProfileScan:
    """A completed *Core verified* walk, not a provider's inventory coverage."""
    identity_id: str
    complete: bool
    evidence_ref: str = ""

    def __post_init__(self) -> None:
        if (not _id(self.identity_id) or type(self.complete) is not bool
                or not _id(self.evidence_ref, empty=True)
                or (self.complete and not self.evidence_ref)):
            raise ValueError("invalid_profile_scan")


@dataclass(frozen=True, slots=True)
class LocalCandidate:
    """Unproven local match from Core inspection, scoped to one exact right."""
    source: SourceKey
    placement_id: str

    def __post_init__(self) -> None:
        if type(self.source) is not SourceKey or not _id(self.placement_id):
            raise ValueError("invalid_local_candidate")


@dataclass(frozen=True, slots=True)
class ConvergenceDecision:
    source: SourceKey
    state: DecisionState
    reason: str
    placement_ids: tuple[str, ...] = ()

    @property
    def acquisition_authorized(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ConvergencePlan:
    decisions: tuple[ConvergenceDecision, ...]
    read_only: bool = True
    acquisition_authorized: bool = False


def plan_convergence(
    snapshots: tuple[ProviderRightsSnapshot, ...],
    *,
    placements: tuple[PlacementEvidence, ...] = (),
    origins: tuple[OriginEvidence, ...] = (),
    scans: tuple[ProfileScan, ...] = (),
    candidates: tuple[LocalCandidate, ...] = (),
    consent_references: frozenset[SourceKey] = frozenset(),
) -> ConvergencePlan:
    """Plan positives only. No filesystem/network/store access or write permit.

    The caller must explicitly supply complete filesystem coverage and local
    candidates, including possible same-title alternatives. Explicit consent is\n    keyed to the exact right (and its kind), not just a shared account. 'missing_to_download'
    remains a *proposal*, not permission. Executor owns fresh locks/rights/consent.
    """
    if (type(snapshots) is not tuple or len(snapshots) > 64
            or any(type(x) is not ProviderRightsSnapshot for x in snapshots)
            or type(placements) is not tuple or len(placements) > 100_000
            or any(type(x) is not PlacementEvidence for x in placements)
            or type(origins) is not tuple or len(origins) > 100_000
            or any(type(x) is not OriginEvidence for x in origins)
            or type(scans) is not tuple or len(scans) > 128
            or any(type(x) is not ProfileScan for x in scans)
            or type(candidates) is not tuple or len(candidates) > 100_000
            or any(type(x) is not LocalCandidate for x in candidates)
            or type(consent_references) is not frozenset
            or any(type(x) is not SourceKey for x in consent_references)):
        raise ValueError("invalid_convergence_input")
    scan_by_identity = {scan.identity_id: scan for scan in scans}
    if len(scan_by_identity) != len(scans):
        raise ValueError("ambiguous_profile_scan")

    # The same bytes in another identity are not a placement for this one.
    local_by_identity_sha: dict[tuple[str, str], list[PlacementEvidence]] = {}
    for placement in placements:
        local_by_identity_sha.setdefault(
            (placement.identity_id, placement.sha256), [],
        ).append(placement)
    origins_by_source: dict[SourceKey, set[str]] = {}
    for origin in origins:
        if origin.kind == "provider" and origin.attested:
            assert origin.source is not None
            origins_by_source.setdefault(origin.source, set()).add(origin.artifact_sha256)
    candidates_by_source: dict[SourceKey, set[str]] = {}
    for candidate in candidates:
        candidates_by_source.setdefault(candidate.source, set()).add(candidate.placement_id)

    decisions = []
    seen: set[SourceKey] = set()
    for snapshot in snapshots:
        # A failed/partial remote inventory never yields negative rights.
        for row in snapshot.rights:
            key = SourceKey(snapshot.scope, row.edition_id, row.right_id, row.kind)
            if key in seen:
                raise ValueError("duplicate_source_reference")
            seen.add(key)
            def decision(state: DecisionState, reason: str, ids: tuple[str, ...] = ()) -> None:
                decisions.append(ConvergenceDecision(key, state, reason, ids))

            if row.state != "active":
                decision("inactive" if row.state == "inactive" else "pending",
                         "right_inactive" if row.state == "inactive" else "right_unconfirmed")
                continue
            if not row.provider_attested:
                decision("pending", "provider_right_unattested")
                continue
            hashes = origins_by_source.get(key, set())
            matching = [
                placement for sha in hashes
                for placement in local_by_identity_sha.get((key.scope.identity_id, sha), ())
            ]
            verified = sorted({p.placement_id for p in matching if p.verified})
            if verified:
                decision("present_attested", "exact_origin_verified_in_profile", tuple(verified))
                continue
            if matching or candidates_by_source.get(key):
                decision("needs_reconciliation", "local_evidence_unverified")
                continue
            scan = scan_by_identity.get(key.scope.identity_id)
            if scan is None or not scan.complete:
                decision("pending", "local_scan_incomplete")
                continue
            if row.delivery == "available" and key in consent_references:
                decision("missing_to_download", "no_local_proof_after_complete_scan")
            else:
                decision("pending", "delivery_or_consent_unavailable")
    return ConvergencePlan(tuple(decisions))


__all__ = [
    "SourceKey", "PlacementEvidence", "OriginEvidence", "ProfileScan",
    "LocalCandidate", "ConvergenceDecision", "ConvergencePlan",
    "plan_convergence",
]
