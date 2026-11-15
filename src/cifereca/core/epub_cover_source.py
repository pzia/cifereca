"""N1 EPUB source evidence that does not depend on physical N3 cache hits.

When Core has already verified a real Artifact+Placement, the persisted
inspection metadata links that exact EPUB to an embedded cover. Even if
its extracted image was evicted from epub-covers-v1, this N1 link remains
a candidate: N3 must attempt rehydration before an origin/catalog fallback.
No EPUB open, image read, GET, or catalogue/rights mutation occurs here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .book_cover_projection import CoverDocumentScope
from .covers import CoverCandidate, candidate
from .epub_cover_materialization import ExactEpubCoverResource
from ..epub_metadata import SCHEMA as EPUB_METADATA_SCHEMA, cover_cache_dir


@dataclass(frozen=True, slots=True)
class AttestedEmbeddedCover:
    """One documentary N1 candidate and its independent physical N3 source."""

    candidate: CoverCandidate
    resource: ExactEpubCoverResource


def project_exact_embedded_cover(
    scope: CoverDocumentScope, *, revision: int,
    artifact_sha256: str, artifact_metadata: dict[str, object],
) -> AttestedEmbeddedCover | None:
    """Project an already-attested exact Artifact+Placement without probing N3.

    This function MUST only receive Core-owned, verified artifact metadata
    and the matching ownership-scoped Placement. It will not convert a
    catalogue thumbnail, arbitrary EPUB path, ISBN or common SHA into a
    link to another document. The caller must validate the placement before
    invoking the N3 recovery worker with its physical source path.
    """
    if (type(scope) is not CoverDocumentScope
            or scope.kind != "placement"
            or type(artifact_metadata) is not dict):
        return None
    if (artifact_metadata.get("epub_metadata_schema") != EPUB_METADATA_SCHEMA
            or artifact_metadata.get("epub_status") != "ok"
            or artifact_metadata.get("epub_cover_status") != "extracted"):
        return None
    data = artifact_metadata.get("epub_cover")
    if (type(data) is not dict or data.get("source") != "epub"):
        return None
    try:
        resource = ExactEpubCoverResource(
            scope=scope, source_revision=revision,
            artifact_sha256=artifact_sha256, member=data.get("member"),
            cache_name=data.get("cache_name"),
        )
    except (ValueError, TypeError):
        return None
    # N1 only constructs the known content-addressed URI; it deliberately
    # does not stat, hash or decode the N3 image. Failure to materialize does
    # not negate the exact book->source evidence.
    uri = (cover_cache_dir().resolve() / resource.cache_name).as_uri()
    proof = candidate(
        uri, tier="embedded", source="epub",
        evidence_id=scope.artifact_id, evidence_strength=100,
    )
    if proof is None:
        return None
    return AttestedEmbeddedCover(proof, resource)


__all__ = ["AttestedEmbeddedCover", "project_exact_embedded_cover"]
