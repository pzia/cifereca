"""Versioned, source-scoped Core cover projection for P1 #6168 (N1).

This is a read-only projection contract, NOT a cross-source document join.
Callers must supply a stable, exactly attested SourceKey / loan / Placement;
title, ISBN, a shared SHA or visual similarity do not establish that scope.
The source revision is owned by the producer; later N2 must invalidate it on
catalogue, credential, Artifact, Placement, OriginLink and loan changes.
No I/O or GTK object is accessed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping

from .covers import CoverCandidate, resolve_cover


ScopeKind = Literal["catalogue", "public_catalogue", "loan", "placement"]
CoverStatus = Literal["selected", "missing", "pending", "ambiguous"]
CoverReason = Literal[
    "attested", "complete_no_admissible_cover", "conflicting_evidence",
    "partial_snapshot", "source_failed", "source_overflow",
]
_MAX_CANDIDATES = 4096


@dataclass(frozen=True, slots=True)
class CoverDocumentScope:
    """Exact origin of ONE book row. Same title/ISBN is not the same scope.

    `source_key` must be generated from the producer's exact, normalized
    coordinates; this class does not infer documentary equivalence.
    Public catalogue sources use an anonymous credential and no profile.
    `account_id=""` may represent an explicitly known primary borrower;
    `None` represents an unknown/non-applicable account.
    """

    kind: ScopeKind
    identity_id: str | None
    source_key: str
    provider_id: str = ""
    credential_id: str = ""
    account_id: str | None = None
    loan_id: str = ""
    artifact_id: str = ""
    placement_id: str = ""

    def __post_init__(self) -> None:
        if (type(self.kind) is not str
                or self.kind not in ("catalogue", "public_catalogue", "loan", "placement")):
            raise ValueError("invalid cover scope kind")
        for field_name in (
            "source_key", "provider_id", "credential_id",
            "loan_id", "artifact_id", "placement_id",
        ):
            value = getattr(self, field_name)
            if (type(value) is not str or len(value) > 512
                    or not value.isprintable()):
                raise ValueError("invalid cover scope field: " + field_name)
        if (self.account_id is not None and
                (type(self.account_id) is not str
                 or len(self.account_id) > 512
                 or not self.account_id.isprintable())):
            raise ValueError("invalid account scope")
        if not self.source_key:
            raise ValueError("cover scope requires exact source key")
        if self.kind == "public_catalogue":
            if (self.identity_id is not None or self.credential_id != "__public__"
                    or self.account_id is not None or not self.provider_id
                    or self.loan_id or self.artifact_id or self.placement_id):
                raise ValueError("public catalogue cover requires anonymous source")
        elif (type(self.identity_id) is not str or not self.identity_id
                or len(self.identity_id) > 512 or not self.identity_id.isprintable()):
            raise ValueError("cover scope requires exact identity")
        if self.kind == "catalogue" and not self.provider_id:
            raise ValueError("catalogue cover requires a source provider")
        if (self.kind == "loan" and (
                not self.provider_id or not self.credential_id
                or self.account_id is None or not self.loan_id)):
            raise ValueError("loan cover requires exact borrower and loan")
        if (self.kind == "placement" and (
                not self.artifact_id or not self.placement_id)):
            raise ValueError("local cover requires exact Artifact and Placement")


@dataclass(frozen=True, slots=True)
class ResolvedBookCover:
    """Immutable N1 decision; revision belongs to the exact source snapshot."""

    scope: CoverDocumentScope
    revision: int
    status: CoverStatus
    reason: CoverReason
    uri: str = ""
    tier: str = ""
    source: str = ""
    evidence_id: str = ""
    candidate_count: int = 0
    schema: str = "cifereca.resolved-book-cover/1"


def resolve_book_cover(
    scope: CoverDocumentScope,
    candidates: Iterable[CoverCandidate],
    *,
    revision: int,
    snapshot_complete: bool,
    attested_book_identity: str = "",
    catalog_priorities: Mapping[str, int] | None = None,
) -> ResolvedBookCover:
    """Decide identically for all consumers of one exact scoped snapshot.

    A partial, failing, or oversized iterator MUST NOT yield `missing`.
    Catalogue tie-breaking still requires separate same-book attestation in
    `resolve_cover`; source keys are never automatically promoted to proof.
    """

    if type(scope) is not CoverDocumentScope:
        raise TypeError("expected an exact CoverDocumentScope")
    # Frozen dataclasses can be altered with object.__setattr__ after admission.
    # Revalidate the complete exact source/authority before N1 resolution.
    scope.__post_init__()
    if type(revision) is not int or revision < 0:
        raise ValueError("source revision must be a nonnegative integer")
    if type(snapshot_complete) is not bool:
        raise TypeError("snapshot completeness must be explicit")

    def pending(reason: CoverReason) -> ResolvedBookCover:
        return ResolvedBookCover(scope, revision, "pending", reason)

    if not snapshot_complete:
        return pending("partial_snapshot")

    materialized: list[CoverCandidate] = []
    try:
        for index, item in enumerate(candidates):
            if index >= _MAX_CANDIDATES:
                return pending("source_overflow")
            materialized.append(item)
    except Exception:
        return pending("source_failed")

    resolution = resolve_cover(
        materialized,
        attested_book_identity=attested_book_identity,
        catalog_priorities=catalog_priorities,
    )
    if resolution.status == "selected":
        return ResolvedBookCover(
            scope, revision, "selected", "attested", uri=resolution.url,
            tier=resolution.tier, source=resolution.source,
            evidence_id=resolution.evidence_id,
            candidate_count=len(resolution.candidates),
        )
    if resolution.status == "ambiguous":
        return ResolvedBookCover(
            scope, revision, "ambiguous", "conflicting_evidence",
            tier=resolution.tier,
            candidate_count=len(resolution.candidates),
        )
    return ResolvedBookCover(
        scope, revision, "missing", "complete_no_admissible_cover",
    )


__all__ = [
    "CoverDocumentScope", "ResolvedBookCover", "resolve_book_cover",
]
