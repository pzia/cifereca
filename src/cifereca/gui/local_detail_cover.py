"""N1 exact Core cover evidence admission for shared GTK detail views (#6168).

A descriptive local Library or Reader row is not itself an Artifact/Placement
proof. Accept only the already-Core-attested, owner-scoped evidence packet.
No filesystem/HTTP/SQLite access and no title, ISBN, SHA-only or URL join.
"""
from __future__ import annotations

from collections.abc import Mapping

from ..core.local_cover_evidence import VerifiedLocalCoverEvidence


def exact_local_detail_cover(
    source: object, *, active_identity_id: object,
    artifact_id: object = None, placement_id: object = None,
) -> VerifiedLocalCoverEvidence | None:
    """Admit the owner-specific packet for this precise detail selection.

    Cross-profile global Library is read-only bibliographic display, not a
    license to materialize another profile's private EPUB image. The N3
    physical bytes remain shared once an independently proved scope exists.
    """
    if (type(source) is not dict or type(active_identity_id) is not str
            or not active_identity_id or source.get("identity_id") != active_identity_id
            or (source.get("_library_view_owner") not in (None, active_identity_id))):
        return None
    facts = source.get("_exact_local_cover_evidence")
    if type(facts) is not VerifiedLocalCoverEvidence:
        return None
    scope = facts.scope
    if (scope.kind != "placement"
            or scope.identity_id != active_identity_id
            or scope.source_key != source.get("key")
            or scope.artifact_id != source.get("artifact_id")
            or scope.placement_id != source.get("placement_id")):
        return None
    if (artifact_id is not None and scope.artifact_id != artifact_id
            or placement_id is not None and scope.placement_id != placement_id):
        return None
    return facts


def exact_recent_detail_cover(
    source: object, *, active_identity_id: object,
) -> VerifiedLocalCoverEvidence | None:
    """Admit only a Core-originated Recent Artifact+Placement packet.

    The Recent display key is the Placement ID, unlike Library's canonical
    source key. Never use this display key, title, ISBN, URL or an N3 image
    to fabricate a documentary association. The source key and EPUB digest
    must both be the canonical Core Artifact form already on the packet.
    """
    if (type(source) is not dict
            or type(active_identity_id) is not str or not active_identity_id
            or source.get("identity_id") != active_identity_id
            or source.get("_library_view_owner") not in (None, active_identity_id)):
        return None
    facts = source.get("_exact_local_cover_evidence")
    if type(facts) is not VerifiedLocalCoverEvidence:
        return None
    scope = facts.scope
    if (scope.kind != "placement"
            or scope.identity_id != active_identity_id
            or scope.artifact_id != source.get("artifact_id")
            or scope.placement_id != source.get("placement_id")
            or scope.source_key != "artifact:sha256:" + facts.artifact_sha256
            or scope.artifact_id != "sha256:" + facts.artifact_sha256):
        return None
    return facts


__all__ = ["exact_local_detail_cover", "exact_recent_detail_cover"]
