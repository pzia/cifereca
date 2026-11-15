"""Exact Core-local document admission for GTK detail (#6168).

The detail pane must use the same N2 DocumentRef as its list cell, never
first fetch an old displayed URL and then switch to an EPUB texture.
This read-side helper only consumes the private, Core-attested packet
after the source row has passed the identity/Artifact/Placement gate.

No catalogue scan, ZIP read, filesystem hash, supplier GET, rights mutation,
title/ISBN join or N3 cache probing occurs here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core.book_cover_projection import CoverDocumentScope
from ..core.local_cover_evidence import VerifiedLocalCoverEvidence
from .document_cover_service import DocumentCoverService


@dataclass(frozen=True, slots=True)
class LocalDetailCoverSelection:
    """Separate no proof, accepted proof and rejected/stale proof.

    When block_legacy=True, the detail must not start an HTTP image load
    from the row's legacy thumbnail. Even a rejected proof cannot authorize
    a downgrade that bypasses the EPUB-first N1 source decision.
    """

    block_legacy: bool
    binding: tuple[DocumentCoverService, CoverDocumentScope] | None = None


NO_LOCAL_PROOF = LocalDetailCoverSelection(False)
BLOCKED_LOCAL_PROOF = LocalDetailCoverSelection(True)


def admit_local_detail_cover(
    window: Any, row: object, *,
    expected_key: str | None = None,
    expected_artifact_id: str | None = None,
    expected_placement_id: str | None = None,
) -> LocalDetailCoverSelection:
    if type(row) is not dict or "_exact_local_cover_evidence" not in row:
        return NO_LOCAL_PROOF
    facts = row["_exact_local_cover_evidence"]
    if type(facts) is not VerifiedLocalCoverEvidence:
        return BLOCKED_LOCAL_PROOF
    scope = facts.scope
    active = getattr(
        getattr(getattr(window, "service", None), "current_identity", None),
        "identity_id", None,
    )
    # The read-only global Library may display A's metadata while B is
    # selected. A foreign descriptive row never confers A's local file or
    # EPUB cover source. N3's shared SHA cache is not a rights witness.
    if (type(active) is not str or not active
            or scope.kind != "placement"
            or scope.identity_id != active
            or row.get("identity_id") != scope.identity_id
            or row.get("artifact_id") != scope.artifact_id
            or row.get("placement_id") != scope.placement_id
            or row.get("key") != scope.source_key
            or expected_key is not None and expected_key != scope.source_key
            or expected_artifact_id is not None
               and expected_artifact_id != scope.artifact_id
            or expected_placement_id is not None
               and expected_placement_id != scope.placement_id):
        return BLOCKED_LOCAL_PROOF
    service = getattr(window, "document_covers", None)
    adapter = getattr(window, "local_cover_adapter", None)
    if type(service) is not DocumentCoverService or adapter is None:
        return BLOCKED_LOCAL_PROOF
    try:
        registered = adapter.register(service, facts)
    except (TypeError, ValueError, RuntimeError):
        return BLOCKED_LOCAL_PROOF
    if registered != scope:
        return BLOCKED_LOCAL_PROOF
    return LocalDetailCoverSelection(True, (service, scope))


__all__ = [
    "LocalDetailCoverSelection", "NO_LOCAL_PROOF",
    "BLOCKED_LOCAL_PROOF", "admit_local_detail_cover",
]
