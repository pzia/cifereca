"""#4982 durable relink of an existing public OriginLink group.

Both request and final transaction rediscover the complete eligible group
from the canonical source and live v14 publication graph. No member list,
private graph coordinates, provider/session callback or network is stored
in the job. Initial group creation and split/shrink are deliberately not
admitted by this worker; the existing live producer retains its behavior.
"""
from __future__ import annotations

from hashlib import sha256

from . import notice_c_job_journal as journal
from .book_cover_projection import CoverDocumentScope
from .notice_c_aggregate_inputs import _capture_txn
from .notice_c_origin_recompute import _group_members
from .notice_c_origin_link_producer import (
    _public_source_coordinates, _purchase_coordinates,
    _admitted_public_sources_txn, _eligible_origin_groups, _MAX_ORIGINS,
)
from .notice_c_cover_index import (
    _resolve_origin_linked_public_group_txn,
    _persist_origin_linked_public_cover_group_txn,
)
from .notice_c_relink_inputs import capture_public_relink_inputs_txn

IMPLEMENTATION_DIGEST = sha256(b"core.origin-linked-public-c-relink.v1").hexdigest()


def _resolve_extension_txn(db, anchor):
    """Select a strict superset of existing, reattested members or refuse.

    Reuse the live producer's positive purchase-receipt/Artifact/Placement
    rules and ambiguity veto. Candidate text cannot establish a relation.
    The caller owns the read or write snapshot; nothing is persisted here.
    """
    if not db.in_transaction:
        raise ValueError("notice_relink_transaction_required")
    old = _capture_txn(db, anchor)
    members = _group_members(old)
    current = {}
    for source in old.sources:
        coords = _public_source_coordinates(source)
        if coords is None or coords in current:
            raise ValueError("notice_relink_relation_changed")
        current[coords] = source

    from .library_projection import _read_provider_origin_coordinates
    verified = _read_provider_origin_coordinates(db, identity_id=None, with_kind=True)
    if not verified or len(verified) > _MAX_ORIGINS:
        raise ValueError("notice_relink_relation_changed")
    wanted = _purchase_coordinates(verified)
    if not current.keys() <= wanted:
        raise ValueError("notice_relink_relation_changed")
    public = _admitted_public_sources_txn(db, wanted)
    if any(public.get(coord) != source for coord, source in current.items()):
        raise ValueError("notice_relink_relation_changed")
    matches = [
        group for ordered, group in _eligible_origin_groups(verified, public, current)
        if current.keys() < set(ordered)
    ]
    if len(matches) != 1:
        raise ValueError("notice_relink_extension_not_qualified")
    sources, = matches
    # The vector includes newly admitted members AND all overlapping C rows.
    vector = capture_public_relink_inputs_txn(db, anchor, sources)
    # Use exactly the writer's canonical conflict/target rule as preflight.
    # Never queue an extension already known to have a competing C.
    _edition, refs, proof = _resolve_origin_linked_public_group_txn(
        db, sources=sources, expected_document=anchor,
    )
    if (not {member.source_ref for member in members} < refs
            or any(member.relation_proof_id != proof for member in members)):
        raise ValueError("notice_relink_relation_changed")
    return vector, sources, proof


def request_origin_public_relink(storage, anchor, *, now, expected_generation):
    """Explicit trusted Core request, group and journal in same transaction."""
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        vector, _sources, _proof = _resolve_extension_txn(db, anchor)
        spec = journal.public_job_spec(
            "relink_notices", anchor, input_digest=vector.input_digest,
            implementation_digest=IMPLEMENTATION_DIGEST,
        )
        return journal._request_job_txn(
            db, spec, now=now, expected_generation=expected_generation,
        )


def prepare_origin_public_relink(storage, spec):
    """Prepare a strict group extension, then rediscover under commit CAS."""
    journal._check_spec(spec)
    if (spec.kind != "relink_notices"
            or spec.implementation_digest != IMPLEMENTATION_DIGEST
            or spec.dependencies):
        raise ValueError("notice_relink_unsupported_spec")
    anchor = CoverDocumentScope(
        kind="public_catalogue", identity_id=None,
        source_key=spec.target, provider_id="cifereca.c",
        credential_id="__public__",
    )
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        vector, _sources, _proof = _resolve_extension_txn(db, anchor)
        if vector.input_digest != spec.input_digest:
            raise ValueError("notice_relink_inputs_changed")

    def publish(db):
        current, sources, proof = _resolve_extension_txn(db, anchor)
        if current != vector or current.input_digest != spec.input_digest:
            raise ValueError("notice_relink_inputs_changed")
        updated = _persist_origin_linked_public_cover_group_txn(
            db, sources=sources, expected_document=anchor,
        )
        expected_epochs = {source.document.source_key: source.epoch
                           for source in sources}
        if (updated.document != anchor
                or {member.source_ref: member.source_epoch
                    for member in updated.sources} != expected_epochs
                or any(member.visibility != "public"
                       or member.public_bibliography is None
                       or member.relation_proof_id != proof
                       for member in updated.sources)):
            raise ValueError("notice_relink_invalid_publication")
        return True

    return publish
