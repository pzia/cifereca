"""#6366: Core-attested public C cover from a local completed OriginLink.

Library and Recent are *owner-scoped local* views. Their catalogue fallback
may reuse a canonical PUBLIC Notice C for completed purchases **or loans**
only when a real completed provider OriginLink relates the Artifact/Placement
to the exact supplier record
retained inside the C. No catalogue match by title, ISBN, URL, SHA alone or
foreign profile. This runs on Library/Recent's existing worker, never GTK.
No right is acquired, inferred, or copied from C. EPUB N1 always wins.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import sqlite3

from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import (
    _PREFIX, _decode, _json, canonical_cover_document_ref,
    is_canonical_cover_document,
)

_MAX_CANONICAL = 4096
_MAX_ROWS = 10000


@dataclass(frozen=True, slots=True)
class VerifiedLocalPublicCLink:
    """One owner+Artifact+Placement and its independently attested public C."""

    identity_id: str
    artifact_id: str
    placement_id: str
    document: CoverDocumentScope
    epoch: int
    source_refs: tuple[str, ...]


def valid_local_public_c_link(
    packet: object, *,
    identity_id: object, artifact_id: object, placement_id: object,
) -> bool:
    """GTK-safe exact read-only check; origin proof was created by Core."""
    return (type(packet) is VerifiedLocalPublicCLink
            and type(identity_id) is str and bool(identity_id)
            and type(artifact_id) is str and bool(artifact_id)
            and type(placement_id) is str and bool(placement_id)
            and packet.identity_id == identity_id
            and packet.artifact_id == artifact_id
            and packet.placement_id == placement_id
            and is_canonical_cover_document(packet.document)
            and type(packet.epoch) is int and packet.epoch > 0
            and type(packet.source_refs) is tuple
            and 1 <= len(packet.source_refs) <= 32
            and all(type(ref) is str and ref.startswith("notice-c:v1:")
                    for ref in packet.source_refs))


def project_local_origin_public_c(
    storage: Any, rows: list[dict[str, Any]], *, identity_id: str,
) -> None:
    """Attach exact public C N2 proof in-place to already-built worker rows.

    The caller has already constructed Library/Recent snapshots. This
    additional bounded read neither looks up files nor consults a supplier.
    Invalid/ambiguous provenance simply leaves the existing cover path.
    """
    if type(rows) is not list or len(rows) > _MAX_ROWS:
        return
    # A repeated worker projection may receive previously annotated rows.
    # A proof from an earlier origin/C inventory cannot survive failed or
    # conflicting fresh evidence, a profile switch, or a removed Placement.
    # Always revoke the old binding before attempting the new admission.
    for row in rows:
        if type(row) is dict:
            row.pop("_public_canonical_cover_link", None)
    if type(identity_id) is not str or not identity_id or not rows:
        return
    wanted = {
        (row.get("artifact_id"), row.get("placement_id"))
        for row in rows if type(row) is dict
        and row.get("identity_id") == identity_id
        and type(row.get("artifact_id")) is str
        and type(row.get("placement_id")) is str
        and row["artifact_id"] and row["placement_id"]
        and row.get("_exact_local_cover_evidence") is None
        and row.get("_library_view_owner") in (None, identity_id)
    }
    if not wanted:
        return

    from .library_projection import _verified_provider_origin_coordinates
    verified = _verified_provider_origin_coordinates(
        storage, identity_id=identity_id, with_kind=True,
    )
    if not verified:
        return

    # The v14 projector has already checked a present owner Placement,
    # completed provider OriginLink, positive source response, artifact
    # structure and delivery receipt; it returns no private URLs.
    by_artifact: dict[str, set[tuple[str, str]]] = {}
    origin_memberships: dict[tuple[str, str], set[str]] = {}
    for artifact, owner, provider, _credential, _account, edition, kind in verified:
        # A completed *loan* acquisition also proves the source-to-Artifact
        # relationship. C remains descriptive, never a borrowing entitlement;
        # an unmaterialized/remote loan still has no Placement and cannot join.
        if owner != identity_id or kind not in ("purchase", "loan"):
            continue
        coord = (provider, edition)
        by_artifact.setdefault(artifact, set()).add(coord)
        origin_memberships.setdefault(coord, set()).add(artifact)

    eligible_artifacts = {artifact for artifact, _placement in wanted}
    if not (eligible_artifacts & by_artifact.keys()):
        return
    try:
        present = {
            (item.get("artifact_id"), item.get("placement_id"))
            for item in storage.placements(state="present")
            if type(item) is dict and item.get("identity_id") == identity_id
        }
        with storage._lock, storage._connect() as db:
            indexed = db.execute(
                "SELECT key,value FROM metadata WHERE key>=? AND key<? "
                "ORDER BY key LIMIT ?",
                (_PREFIX, _PREFIX + "\uffff", _MAX_CANONICAL + 1),
            ).fetchall()
    except (sqlite3.Error, AttributeError, TypeError, ValueError):
        return
    if len(indexed) > _MAX_CANONICAL:
        return

    # For every real public canonical C, ALL its source bibliographies
    # must be independently proven to belong to this Artifact. Otherwise
    # no single local file may borrow another edition's public thumbnail.
    candidates: dict[str, list[tuple[object, int]]] = {}
    for key, raw in indexed:
        try:
            value = _json(raw)
            document = canonical_cover_document_ref(value["edition_id"])
            if key != _PREFIX + document.source_key.rsplit(":", 1)[-1]:
                continue
            c = _decode(raw, document)
            sources = c.sources
            if not sources or not all(
                source.visibility == "public"
                and source.public_bibliography is not None
                for source in sources
            ):
                continue
            coords = tuple(
                (source.provider_id, source.public_bibliography.record_id)
                for source in sources
            )
            if len(set(coords)) != len(coords):
                continue
            for artifact in eligible_artifacts:
                trusted = by_artifact.get(artifact, set())
                if all(coord in trusted
                       and origin_memberships.get(coord) == {artifact}
                       for coord in coords):
                    candidates.setdefault(artifact, []).append(
                        (c, len(coords)),
                    )
        except (ValueError, KeyError, TypeError, AttributeError):
            continue

    selected: dict[str, object] = {}
    for artifact, options in candidates.items():
        # Source-count is not evidence that one *contradictory* canonical C
        # is truer than another. One multi-source relation supersedes the
        # automatic self-C rows; any two multi-source relations for this
        # Artifact are ambiguous, even when their sizes differ (3 vs 2).
        relations = [c for c, count in options if count > 1]
        if len(relations) == 1:
            selected[artifact] = relations[0]
        elif not relations and len(options) == 1:
            selected[artifact] = options[0][0]

    for row in rows:
        if (type(row) is not dict
                or row.get("identity_id") != identity_id
                or row.get("_library_view_owner") not in (None, identity_id)
                or row.get("_exact_local_cover_evidence") is not None):
            continue
        artifact, placement = row.get("artifact_id"), row.get("placement_id")
        if (artifact, placement) not in wanted or (artifact, placement) not in present:
            continue
        consolidated = selected.get(artifact)
        if consolidated is None:
            continue
        row["_public_canonical_cover_link"] = VerifiedLocalPublicCLink(
            identity_id=identity_id,
            artifact_id=artifact, placement_id=placement,
            document=consolidated.document,
            epoch=consolidated.epoch,
            source_refs=tuple(src.source_ref for src in consolidated.sources),
        )


__all__ = [
    "VerifiedLocalPublicCLink", "valid_local_public_c_link",
    "project_local_origin_public_c",
]
