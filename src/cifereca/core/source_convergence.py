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
    "present_attested", "present_declared", "needs_reconciliation", "missing_to_download",
    "pending", "inactive", "skipped_archived",
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
    branch: str

    def __post_init__(self) -> None:
        if (type(self.scope) is not ProviderRightsScope
                or not _id(self.edition_id) or not _id(self.right_id)
                or type(self.kind) is not str
                or self.kind not in ("purchase", "loan")
                or not _id(self.branch)):
            raise ValueError("invalid_source_reference")


@dataclass(frozen=True, slots=True)
class PlacementEvidence:
    """B: Core's last verification result, scoped to a physical profile copy."""
    identity_id: str
    placement_id: str
    sha256: str
    size: int
    verified: bool
    verification_ref: str = ""

    def __post_init__(self) -> None:
        if (not _id(self.identity_id) or not _id(self.placement_id)
                or type(self.sha256) is not str or len(self.sha256) != 64
                or any(ch not in "0123456789abcdef" for ch in self.sha256)
                or type(self.size) is not int or self.size < 0
                or type(self.verified) is not bool
                or not _id(self.verification_ref, empty=True)
                or (self.verified and not self.verification_ref)):
            raise ValueError("invalid_placement_evidence")


@dataclass(frozen=True, slots=True)
class OriginEvidence:
    """B history: many contextual links may target one globally shared SHA.

    Each link has an independent Core-owned identifier and an origin profile.
    A provider link must match the exact right's owning identity; a transfer,
    ADEPT or Cifereca origin cannot become a provider authorization.
    """
    kind: OriginKind
    artifact_sha256: str
    source: SourceKey | None = None
    attested: bool = False
    identity_id: str = ""
    origin_ref: str = ""
    proof_ref: str = ""
    parent_sha256: str | None = None

    def __post_init__(self) -> None:
        valid_sha = lambda sha: (
            type(sha) is str and len(sha) == 64
            and all(ch in "0123456789abcdef" for ch in sha)
        )
        if (self.kind not in ("provider", "manual", "transfer", "cifereca", "adept", "derived")
                or type(self.kind) is not str
                or not valid_sha(self.artifact_sha256)
                or (self.source is not None and type(self.source) is not SourceKey)
                or type(self.attested) is not bool
                or not _id(self.identity_id) or not _id(self.origin_ref)
                or not _id(self.proof_ref, empty=True)
                or (self.attested and not self.proof_ref)
                or (self.kind == "provider" and (
                    self.source is None
                    or self.source.scope.identity_id != self.identity_id))
                or (self.kind == "derived" and (
                    not valid_sha(self.parent_sha256)
                    or self.parent_sha256 == self.artifact_sha256))
                or (self.kind != "derived" and self.parent_sha256 is not None)):
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
class DeclaredAssociationEvidence:
    """Core-only user declaration, never an origin/right/receipt.

    'declared_current' requires a fresh file check by Core. The verification
    reference is distinct from the user's recorded association identifier.
    Revoked links are history only; stale links require reconciliation.
    """
    source: SourceKey
    relation_id: str
    placement_id: str
    status: Literal["declared_current", "needs_review", "revoked"]
    verification_ref: str = ""
    identity_id: str = ""

    def __post_init__(self) -> None:
        if (type(self.source) is not SourceKey
                or self.identity_id != self.source.scope.identity_id
                or not _id(self.identity_id)
                or not _id(self.relation_id) or not _id(self.placement_id)
                or type(self.status) is not str
                or self.status not in ("declared_current", "needs_review", "revoked")
                or not _id(self.verification_ref, empty=True)
                or (self.status == "declared_current" and not self.verification_ref)
                or (self.status != "declared_current" and self.verification_ref)):
            raise ValueError("invalid_declared_association_evidence")


@dataclass(frozen=True, slots=True)
class ConvergenceDecision:
    source: SourceKey
    state: DecisionState
    reason: str
    placement_ids: tuple[str, ...] = ()

    @property
    def acquisition_authorized(self) -> bool:
        return False

    @property
    def authorizes_negative_reconciliation(self) -> bool:
        """A plan is not an authoritative loan return or purchase revocation."""
        return False

    @property
    def provider_satisfied(self) -> bool:
        """Only exact OriginLink(provider) + verified profile bytes satisfy A.

        A checked user declaration remains visible but is never a supplier
        receipt and must not be accepted as a no-download finality.
        """
        return self.state == "present_attested"

    @property
    def independent_acquisition_review_possible(self) -> bool:
        """Incomplete scan does not prove absence or permanently veto a request.

        Requires a separate executor admission, scope/consent/rights check and
        a new local preflight; this flag NEVER grants an automatic download.
        """
        return self.reason in {
            "scan_incomplete_independent_admission_required",
            "local_evidence_independent_admission_required",
            "candidate_only_independent_admission_required",
            "declared_current_independent_admission_required",
        }


@dataclass(frozen=True, slots=True)
class ConvergencePlan:
    decisions: tuple[ConvergenceDecision, ...]

    @property
    def read_only(self) -> bool:
        return True

    @property
    def acquisition_authorized(self) -> bool:
        return False


def plan_convergence(
    snapshots: tuple[ProviderRightsSnapshot, ...],
    *,
    placements: tuple[PlacementEvidence, ...] = (),
    origins: tuple[OriginEvidence, ...] = (),
    scans: tuple[ProfileScan, ...] = (),
    candidates: tuple[LocalCandidate, ...] = (),
    declarations: tuple[DeclaredAssociationEvidence, ...] = (),
    consent_references: frozenset[SourceKey] = frozenset(),
) -> ConvergencePlan:
    """Plan positives only. No filesystem/network/store access or write permit.

    The caller must explicitly supply complete filesystem coverage and local
    candidates, including possible same-title alternatives. Explicit consent is
    keyed to the exact right (and its kind), not just a shared account. 'missing_to_download'
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
            or type(declarations) is not tuple or len(declarations) > 100_000
            or any(type(x) is not DeclaredAssociationEvidence for x in declarations)
            or type(consent_references) is not frozenset
            or any(type(x) is not SourceKey for x in consent_references)):
        raise ValueError("invalid_convergence_input")
    scan_by_identity = {scan.identity_id: scan for scan in scans}
    if len(scan_by_identity) != len(scans):
        raise ValueError("ambiguous_profile_scan")

    # Conflicting reuses of a global placement/origin identifier are corrupt
    # evidence, not a tie to resolve by SHA, title or insertion order.
    by_placement_id: dict[str, PlacementEvidence] = {}
    local_by_identity_sha: dict[tuple[str, str], list[PlacementEvidence]] = {}
    for placement in placements:
        previous = by_placement_id.setdefault(placement.placement_id, placement)
        if previous != placement:
            raise ValueError("conflicting_placement_evidence")
        local_by_identity_sha.setdefault(
            (placement.identity_id, placement.sha256), [],
        ).append(placement)
    by_origin_ref: dict[str, OriginEvidence] = {}
    origins_by_source: dict[SourceKey, set[str]] = {}
    for origin in origins:
        previous = by_origin_ref.setdefault(origin.origin_ref, origin)
        if previous != origin:
            raise ValueError("conflicting_origin_evidence")
        if origin.kind == "provider" and origin.attested:
            assert origin.source is not None
            origins_by_source.setdefault(origin.source, set()).add(origin.artifact_sha256)
    candidates_by_source: dict[SourceKey, set[str]] = {}
    for candidate in candidates:
        candidates_by_source.setdefault(candidate.source, set()).add(candidate.placement_id)

    declared_by_source: dict[SourceKey, list[DeclaredAssociationEvidence]] = {}
    relation_ids: dict[str, DeclaredAssociationEvidence] = {}
    for relation in declarations:
        previous = relation_ids.setdefault(relation.relation_id, relation)
        if previous != relation:
            raise ValueError("conflicting_declared_association")
        if relation.status != "revoked":
            declared_by_source.setdefault(relation.source, []).append(relation)

    decisions = []
    seen: set[SourceKey] = set()
    for snapshot in snapshots:
        # A failed/partial remote inventory never yields negative rights.
        for row in snapshot.rights:
            key = SourceKey(snapshot.scope, row.edition_id, row.right_id,
                            row.kind, snapshot.branch)
            if key in seen:
                raise ValueError("duplicate_source_reference")
            seen.add(key)
            def decision(state: DecisionState, reason: str, ids: tuple[str, ...] = ()) -> None:
                decisions.append(ConvergenceDecision(key, state, reason, ids))

            # An old receipt or unverified legacy state is never authority
            # for ending a right. Even a reported inactive provider state is
            # descriptive here: this read-only plan cannot close, return or
            # revoke a loan/purchase. Explicit negative writes require the
            # separately guarded provider inventory completeness contract.
            if not row.provider_attested:
                decision("pending", "provider_right_unattested")
                continue
            if row.state != "active" and row.delivery != "available":
                # Unknown/inactive is descriptive metadata, not an independent
                # file-delivery veto. A genuinely available provider offer
                # takes precedence; legacy state without an offer stays
                # diagnostic-only and cannot initiate GET.
                decision("inactive" if row.state == "inactive" else "pending",
                         "right_inactive" if row.state == "inactive" else "delivery_unconfirmed")
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
            if row.delivery_unavailable_reason == "archived":
                decision("skipped_archived", "delivery_archived")
                continue
            active_declarations = declared_by_source.get(key, ())
            current = sorted({
                relation.placement_id for relation in active_declarations
                if relation.status == "declared_current"
            })
            stale = any(relation.status != "declared_current"
                        for relation in active_declarations)
            if current and not stale and len(current) == 1:
                # A fresh user-declared match is not a verified provider
                # OriginLink. Keep the declared copy visible, but do not make
                # the statement a permanent no-GET gate: the independent
                # executor can still admit a previously authorized purchase
                # after rechecking exact right, consent, local bytes and lock.
                authorized_offer = (
                    row.delivery == "available" and key in consent_references
                )
                decision(
                    "present_declared",
                    "declared_current_independent_admission_required"
                    if authorized_offer else "fresh_user_declaration_not_provider_proof",
                    tuple(current),
                )
                continue
            # An old receipt/placement or stale user declaration is not proof
            # of satisfaction. Preserve the diagnostic, but never impose a
            # perpetual manual reconciliation veto on an authorized purchase.
            permitted_offer = row.delivery == "available" and key in consent_references
            if matching or active_declarations:
                decision(
                    "needs_reconciliation",
                    "local_evidence_independent_admission_required"
                    if permitted_offer else "local_evidence_unverified",
                )
                continue
            scan = scan_by_identity.get(key.scope.identity_id)
            if scan is None or not scan.complete:
                # An incomplete scan never demonstrates physical absence.
                # A bibliographic candidate remains only a descriptive hint,
                # not a second ownership or acquisition authorization.
                if permitted_offer:
                    decision(
                        "pending",
                        "candidate_only_independent_admission_required"
                        if candidates_by_source.get(key)
                        else "scan_incomplete_independent_admission_required",
                    )
                elif candidates_by_source.get(key):
                    decision("needs_reconciliation", "bibliographic_candidate_unattested")
                else:
                    decision("pending", "local_scan_incomplete")
                continue
            if permitted_offer:
                # "Missing" here means no attested satisfaction for this right,
                # NOT no similarly titled file. Executor must revalidate and
                # may acquire instead of requiring a manual title-based link.
                decision(
                    "missing_to_download",
                    "unattested_candidate_no_satisfaction"
                    if candidates_by_source.get(key)
                    else "no_local_proof_after_complete_scan",
                )
            elif candidates_by_source.get(key):
                decision("needs_reconciliation", "bibliographic_candidate_unattested")
            else:
                decision("pending", "delivery_or_consent_unavailable")
    return ConvergencePlan(tuple(decisions))


__all__ = [
    "SourceKey", "PlacementEvidence", "OriginEvidence", "ProfileScan",
    "LocalCandidate", "DeclaredAssociationEvidence", "ConvergenceDecision", "ConvergencePlan",
    "plan_convergence",
]
