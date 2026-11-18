"""#4982: inert public relink input-vector contract for an existing anchor C.

Unlike recomputation, relink can introduce newly admitted source members.
This captures a candidate group selected by the Core OriginLink producer;
it is NOT evidence that the group is an edition. The eventual executor must
use the same producer's live group/overlap rules and the canonical writer
inside its final transaction. No journal request, dispatch or write here.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import (
    _PREFIX, _db_key, _decode, _json, _public_group_refs,
    canonical_cover_document_ref, is_canonical_cover_document,
)
from .notice_c_origin_link_producer import _public_source_coordinates
from .notice_cover_repository import _checked_source_state, _opaque_key

# Up to 32 individual exact self-C rows plus a shared group are expected.
# More rows are not a complete candidate set and cannot be silently ignored.
_MAX_OVERLAPS = 64


@dataclass(frozen=True, slots=True, repr=False)
class PublicRelinkInputs:
    anchor: CoverDocumentScope
    source_refs: tuple[str, ...]
    input_digest: str
    overlap_count: int


def _hash(value):
    return sha256(value.encode("utf-8")).hexdigest()


def capture_public_relink_inputs_txn(db, anchor, sources):
    """Hash all exact live members and overlapping C rows in one snapshot.

    A relink anchor must be an existing public C whose retained membership
    is contained in the proposed group. Initial self-C creation and
    shrinking/splitting are not part of this contract. No document copies
    or private graph coordinates are carried in the result. In particular
    this digest does not attest OriginLinks; even equal digests require
    fresh positive relation evidence at request AND commit.
    """
    if not db.in_transaction:
        raise ValueError("notice_relink_transaction_required")
    if not is_canonical_cover_document(anchor):
        raise ValueError("notice_relink_invalid_anchor")
    refs = _public_group_refs(sources)
    coords = [_public_source_coordinates(source) for source in sources]
    if (any(coord is None for coord in coords)
            or len({coord[0] for coord in coords}) != len(sources)):
        raise ValueError("notice_relink_invalid_sources")

    source_vector = []
    for source in sorted(sources, key=lambda item: item.document.source_key):
        key = _opaque_key(source.document)
        if key is None:
            raise ValueError("notice_relink_invalid_sources")
        raw, current, fence = _checked_source_state(db, source.document, key)
        if current is None or current != source:
            raise ValueError("notice_relink_source_changed")
        source_vector.append((source.document.source_key, _hash(raw[0]), fence))

    rows = db.execute(
        "SELECT m.key,m.value FROM metadata AS m "
        "WHERE m.key>=? AND m.key<? AND EXISTS ("
        "SELECT 1 FROM json_each(CASE WHEN json_valid(m.value) "
        "THEN m.value ELSE '{}' END, '$.sources') src "
        "WHERE src.key IN (SELECT value FROM json_each(?))) "
        "ORDER BY m.key LIMIT ?",
        (_PREFIX, _PREFIX + "\uffff", json.dumps(sorted(refs)), _MAX_OVERLAPS + 1),
    ).fetchall()
    if len(rows) > _MAX_OVERLAPS:
        raise ValueError("notice_relink_overlap_budget")

    overlap_vector = []
    anchored = False
    for key, raw in rows:
        document = canonical_cover_document_ref(_json(raw)["edition_id"])
        if _db_key(document) != key:
            raise ValueError("notice_relink_invalid_overlap")
        canonical = _decode(raw, document)
        if any(member.visibility != "public" or member.public_bibliography is None
               for member in canonical.sources):
            raise ValueError("notice_relink_nonpublic_overlap")
        if document == anchor:
            if (not canonical.sources
                    or not {member.source_ref for member in canonical.sources} <= refs):
                raise ValueError("notice_relink_anchor_membership_changed")
            anchored = True
        # All qualifying overlaps (even competing groups) enter the CAS.
        # Only the canonical group writer may decide the target/conflict.
        overlap_vector.append((document.source_key, _hash(raw)))
    if not anchored:
        raise ValueError("notice_relink_anchor_missing")

    wire = json.dumps(
        ["cifereca.notice-c.relink-input.v1", anchor.source_key,
         source_vector, overlap_vector],
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )
    return PublicRelinkInputs(anchor, tuple(sorted(refs)), _hash(wire), len(rows))


def check_public_relink_inputs_txn(db, anchor, sources, *, expected_digest):
    """Equality fence only. This MUST NOT be used to authorize a relation."""
    current = capture_public_relink_inputs_txn(db, anchor, sources)
    if type(expected_digest) is not str or current.input_digest != expected_digest:
        raise ValueError("notice_relink_inputs_changed")
    return current
