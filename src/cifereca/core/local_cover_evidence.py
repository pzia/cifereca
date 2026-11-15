"""P1 #6168: attest a local book-to-cover source independently from N3 bytes.

Only Core's library projection, AFTER local_book_path() admitted the exact
owner's Artifact+Placement, may publish this packet. GUI can convert it to
a DocumentCoverService snapshot, but cannot infer a Placement from a title,
ISBN, matching URL, shared hash or foreign descriptive metadata.

The packet is private to the scoped owner projection. The global Library
read-only adapter must never carry it across identities.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping

from .book_cover_projection import CoverDocumentScope
from .covers import CoverCandidate, candidate
from .epub_cover_source import (
    AttestedEmbeddedCover, project_exact_embedded_cover,
)

_SHA = re.compile(r"[a-f0-9]{64}\Z")
_MAX_PUBLIC_CANDIDATES = 256


@dataclass(frozen=True, slots=True)
class VerifiedLocalCoverEvidence:
    """One exact Core placement and its documentary, not physical, sources.

    The embedded resource has source_revision=0 as a neutral producer packet:
    the owning N2 adapter issues a monotonically increasing generation when
    evidence changes. Image/ZIP member are Core-inspected Artifact facts.
    """

    scope: CoverDocumentScope
    artifact_sha256: str
    verified_path: Path
    candidates: tuple[CoverCandidate, ...]
    embedded: AttestedEmbeddedCover | None


def project_verified_local_cover_evidence(
    *,
    identity_id: str,
    book_key: str,
    artifact_id: str,
    placement_id: str,
    artifact_sha256: str,
    artifact_metadata: Mapping[str, object],
    verified_path: Path,
    candidates: tuple[CoverCandidate, ...] = (),
) -> VerifiedLocalCoverEvidence | None:
    """Build N1 evidence from the ALREADY-openable Core Placement.

    verified_path must be the result of Core.local_book_path for the
    same identity and placement. This pure function is not that admission
    check: a caller cannot use it to grant filesystem or borrower rights.
    The Core library projection holds the path proof before invoking it.
    """
    if (type(identity_id) is not str or not identity_id
            or type(book_key) is not str or not book_key
            or type(artifact_id) is not str or not artifact_id
            or type(placement_id) is not str or not placement_id
            or type(artifact_sha256) is not str
            or _SHA.fullmatch(artifact_sha256) is None
            or type(artifact_metadata) is not dict
            or not isinstance(verified_path, Path)
            or not verified_path.is_absolute()
            or type(candidates) is not tuple
            or len(candidates) > _MAX_PUBLIC_CANDIDATES):
        return None
    if (artifact_id.startswith("sha256:")
            and artifact_id[7:] != artifact_sha256):
        return None
    try:
        scope = CoverDocumentScope(
            kind="placement", identity_id=identity_id,
            source_key=book_key, artifact_id=artifact_id,
            placement_id=placement_id,
        )
    except ValueError:
        return None

    public: list[CoverCandidate] = []
    for proof in candidates:
        if type(proof) is not CoverCandidate or proof.tier not in ("origin", "catalog"):
            return None
        checked = candidate(
            proof.url, tier=proof.tier, source=proof.source,
            evidence_id=proof.evidence_id,
            evidence_strength=proof.evidence_strength,
        )
        if checked != proof:
            return None
        if proof not in public:
            public.append(proof)

    embedded = None
    if verified_path.suffix.lower() in (".epub", ".kepub"):
        embedded = project_exact_embedded_cover(
            scope, revision=0, artifact_sha256=artifact_sha256,
            artifact_metadata=dict(artifact_metadata),
        )
    if embedded is None and not public:
        return None
    return VerifiedLocalCoverEvidence(
        scope=scope, artifact_sha256=artifact_sha256,
        verified_path=verified_path, candidates=tuple(public),
        embedded=embedded,
    )


__all__ = [
    "VerifiedLocalCoverEvidence", "project_verified_local_cover_evidence",
]
