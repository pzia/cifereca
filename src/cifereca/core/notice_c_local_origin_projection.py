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
import json
import sqlite3

from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import (
    _PREFIX, _decode, _json, canonical_cover_document_ref,
    is_canonical_cover_document,
)

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
    documentary_only: bool = False,
) -> None:
    """Attach exact public C N2 proof in-place to already-built worker rows.

    The caller has already constructed Library/Recent snapshots. This
    additional bounded read neither looks up files nor consults a supplier.
    Invalid/ambiguous provenance simply leaves the existing cover path.
    The separate opt-in documentary receipt is independent of EPUB cover
    priority; it never creates or changes a cover fallback on the row.
    """
    if type(rows) is not list or type(documentary_only) is not bool:
        return
    receipt_key = ("_public_canonical_document_link" if documentary_only
                   else "_public_canonical_cover_link")
    # A repeated worker projection may receive previously annotated rows.
    # A proof from an earlier origin/C inventory cannot survive failed or
    # conflicting fresh evidence, a profile switch, or a removed Placement.
    # Always revoke the old binding before attempting the new admission.
    for row in rows:
        if type(row) is dict:
            row.pop(receipt_key, None)
    if (len(rows) > _MAX_ROWS or type(identity_id) is not str
            or not identity_id or not rows):
        return
    wanted = {
        (row.get("artifact_id"), row.get("placement_id"))
        for row in rows if type(row) is dict
        and row.get("identity_id") == identity_id
        and type(row.get("artifact_id")) is str
        and type(row.get("placement_id")) is str
        and row["artifact_id"] and row["placement_id"]
        and (documentary_only or row.get("_exact_local_cover_evidence") is None)
        and row.get("_library_view_owner") in (None, identity_id)
    }
    if not wanted:
        return

    from .library_projection import _read_provider_origin_coordinates

    try:
        with storage._lock, storage._connect() as db:
            # One SQLite snapshot for positive origin evidence, present
            # placement and documentary C. No mixed-epoch proof can be
            # assembled from individually valid but disjoint reads.
            db.execute("BEGIN")
            verified = _read_provider_origin_coordinates(
                db, identity_id=identity_id, with_kind=True,
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
            relevant = {
                coord for artifact in eligible_artifacts
                for coord in by_artifact.get(artifact, ())
            }
            if not relevant:
                return
            # The completed OriginLink only attests provider + upstream record ID.
            # It cannot identify a distinct docbase/medium/revision of that record.
            # Across ALL durable public C records, require those coarse coordinates
            # to designate exactly one independently admitted source edition.
            # A self-C and a multi-source C for the same exact edition are compatible.
            exact_editions: dict[tuple[str, str], set[str]] = {}
            qualified: list[tuple[object, tuple[tuple[str, str], ...]]] = []
            corrupt_claims: set[tuple[str, str]] = set()

            present = set(db.execute(
                "SELECT artifact_id,placement_id FROM placements "
                "WHERE identity_id=? AND state='present' "
                "AND json_array(artifact_id,placement_id) IN "
                "(SELECT json(value) FROM json_each(?))",
                (identity_id, json.dumps(sorted(wanted))),
            ).fetchall())
            if not present:
                return
            # Select every canonical row claiming any relevant coarse
            # provider/record, including conflicting editions and groups.
            # JSON is only a prefilter; the Core decoder remains authoritative.
            cursor = db.execute(
                "SELECT m.key,m.value FROM metadata AS m "
                "WHERE m.key>=? AND m.key<? AND EXISTS ("
                "SELECT 1 FROM json_each(CASE WHEN json_valid(m.value) "
                "THEN m.value ELSE '{}' END, '$.sources') AS src WHERE "
                "json_array(json_extract(CASE WHEN src.type='object' "
                "THEN src.value ELSE '{}' END, '$.provider'),"
                "json_extract(CASE WHEN src.type='object' "
                "THEN src.value ELSE '{}' END, '$.public_bibliography.record_id')) "
                "IN (SELECT json(value) FROM json_each(?))) ORDER BY m.key",
                (_PREFIX, _PREFIX + "\uffff", json.dumps(sorted(relevant))),
            )
            for key, raw in cursor:
                # SQL selected this row because it claims a relevant source.
                # A broken canonical claim cannot make a sibling relation
                # appear uniquely qualified by simply failing its decoder.
                claimed = set()
                try:
                    untrusted = json.loads(raw)
                    members = untrusted.get("sources", {})
                    if type(members) is dict:
                        for member in members.values():
                            if type(member) is not dict:
                                continue
                            bio = member.get("public_bibliography")
                            if type(bio) is not dict:
                                continue
                            coord = (member.get("provider"), bio.get("record_id"))
                            if all(type(part) is str for part in coord) and coord in relevant:
                                claimed.add(coord)
                    value = _json(raw)
                    document = canonical_cover_document_ref(value["edition_id"])
                    if key != _PREFIX + document.source_key.rsplit(":", 1)[-1]:
                        corrupt_claims.update(claimed)
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
                    for coord, source in zip(coords, sources):
                        exact_editions.setdefault(coord, set()).add(
                            source.public_bibliography.edition_id
                        )
                    if len(set(coords)) != len(coords):
                        continue
                    qualified.append((c, coords))
                except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
                    corrupt_claims.update(claimed)
                    continue

    except (sqlite3.Error, AttributeError, TypeError, ValueError):
        return

    candidates: dict[str, list[tuple[object, int]]] = {}
    for c, coords in qualified:
        # A provider + record shared by two distinct exact public catalogue
        # editions is insufficient to relate either to the local Artifact.
        if any(coord in corrupt_claims or len(exact_editions[coord]) != 1 for coord in coords):
            continue
        # Each completed OriginLink must belong to the same one Artifact,
        # independently of SHA matches or rights held by another identity.
        linked = origin_memberships.get(coords[0], set())
        if len(linked) != 1:
            continue
        artifact = next(iter(linked))
        if artifact not in eligible_artifacts:
            continue
        trusted = by_artifact.get(artifact, set())
        if all(coord in trusted and origin_memberships.get(coord) == linked
               for coord in coords):
            candidates.setdefault(artifact, []).append((c, len(coords)))

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
                or (not documentary_only and row.get("_exact_local_cover_evidence") is not None)):
            continue
        artifact, placement = row.get("artifact_id"), row.get("placement_id")
        if (artifact, placement) not in wanted or (artifact, placement) not in present:
            continue
        consolidated = selected.get(artifact)
        if consolidated is None:
            continue
        row[receipt_key] = VerifiedLocalPublicCLink(
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
