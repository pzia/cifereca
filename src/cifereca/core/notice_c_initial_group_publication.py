"""#4982: recover a missing first exact OriginLink group, on the existing journal.

The durable anchor is one opaque public source ref, never a private graph
coordinate. Every attempt rediscovers the whole eligible group and its
exact sources under the current graph. Only a missing group can be created;
strict extensions reuse the existing relink port; other existing groups
remain the recomputation port's responsibility.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json

from . import notice_c_job_journal as journal
from .notice_c_initial_publication import _inputs
from .notice_c_origin_link_producer import (
    _public_source_coordinates, _purchase_coordinates, _admitted_public_sources_txn,
    _eligible_origin_groups, _MAX_ORIGINS,
)
from .notice_c_cover_index import (
    canonical_cover_document_ref, _db_key, _decode,
    _resolve_origin_linked_public_group_txn, _persist_origin_linked_public_cover_group_txn,
)
from .notice_cover_repository import PersistedNoticeCover

IMPLEMENTATION_DIGEST = journal._INITIAL_GROUP_C_DIGEST


def _group_inputs(db, source_ref):
    source, _own, _digest = _inputs(db, source_ref)
    coord = _public_source_coordinates(source)
    if coord is None:
        return None
    from .library_projection import _read_provider_origin_coordinates
    verified = _read_provider_origin_coordinates(db, identity_id=None, with_kind=True)
    if not verified or len(verified) > _MAX_ORIGINS:
        return None
    wanted = _purchase_coordinates(verified)
    if coord not in wanted:
        return None
    public = _admitted_public_sources_txn(db, wanted)
    if public.get(coord) != source:
        return None
    groups = [group for _ordered, group in
              _eligible_origin_groups(verified, public, {coord: source})]
    if len(groups) != 1:
        return None
    sources, = groups
    # Use the canonical writer's own overlap/conflict rules, not a new
    # relation algorithm. An extension will resolve to its existing C.
    target, refs, proof = _resolve_origin_linked_public_group_txn(db, sources=sources)
    document = canonical_cover_document_ref(target)
    vector = []
    for member in sorted(sources, key=lambda item: item.document.source_key):
        current, _own, digest = _inputs(db, member.document.source_key)
        if current != member:
            raise ValueError("notice_work_initial_group_source_changed")
        vector.append((member.document.source_key, digest))
    wire = json.dumps(["cifereca.notice-c.initial-group-input.v1",
                       document.source_key, vector], ensure_ascii=True, separators=(",", ":"))
    return document, sources, min(refs), proof, sha256(wire.encode("ascii")).hexdigest()


def request_initial_group_publication(storage, source, *, now):
    """Request initial creation only, never replace an existing group."""
    return _request_group(storage, source, now=now, allow_extension=False)


def request_search_group_repair(storage, source, *, now):
    """After failed live Search consolidation, select initial or strict relink.

    No new job class: existing relink revalidates the same graph/vector.
    A complete existing membership belongs to ordinary recomputation only.
    """
    return _request_group(storage, source, now=now, allow_extension=True)


def _request_group(storage, source, *, now, allow_extension):
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return _request_group_txn(db, source, now=now, allow_extension=allow_extension)


def request_search_group_repair_txn(db, source, *, now):
    """Compose admitted source and group repair in the caller's transaction.

    The caller must own the writer transaction; errors are not swallowed
    and no worker, transport or commit is started by this port.
    """
    return _request_group_txn(db, source, now=now, allow_extension=True)


def _request_group_txn(db, source, *, now, allow_extension):
    if not db.in_transaction:
        raise ValueError("notice_work_transaction_required")
    if type(source) is not PersistedNoticeCover or not journal._integer(now):
        raise ValueError("notice_work_invalid_initial_source")
    if _public_source_coordinates(source) is None:
        return None
    current, _own, _digest = _inputs(db, source.document.source_key)
    if current != source:
        raise ValueError("notice_work_source_changed")
    inputs = _group_inputs(db, source.document.source_key)
    if inputs is None:
        return None
    document, sources, anchor, _proof, digest = inputs
    row = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(document),)).fetchone()
    if row is None:
        spec = journal.public_job_spec(
            "relink_notices", document, input_digest=digest,
            implementation_digest=IMPLEMENTATION_DIGEST, source_ref=anchor,
        )
    else:
        if not allow_extension:
            return None
        previous_c = _decode(row[0], document)
        existing_refs = {member.source_ref for member in previous_c.sources}
        wanted_refs = {member.document.source_key for member in sources}
        if not existing_refs < wanted_refs:
            return None
        from .notice_c_origin_relink import (
            _resolve_extension_txn, IMPLEMENTATION_DIGEST as RELINK_DIGEST,
        )
        # Reuse the exact strict-superset port, including the source and
        # overlap vector. A newly discovered member cannot authorize a
        # competing canonical target or a shrink/split.
        vector, current_sources, _proof = _resolve_extension_txn(db, document)
        if {member.document.source_key for member in current_sources} != wanted_refs:
            raise ValueError("notice_relink_inputs_changed")
        spec = journal.public_job_spec(
            "relink_notices", document, input_digest=vector.input_digest,
            implementation_digest=RELINK_DIGEST,
        )
    previous = journal._load(db, journal._key(spec))
    return journal._request_job_txn(
        db, spec, now=now, expected_generation=previous.ref.generation if previous else 0,
    )


class InitialGroupSuperseded(ValueError):
    """A different, fully attested target has durable successor evidence."""


def _superseding_group_txn(db, spec, inputs):
    if inputs is None:
        return False
    document, sources, anchor, proof, digest = inputs
    if document.source_key == spec.target:
        return False
    # Only positive successor evidence qualifies. Absence, graph conflicts,
    # and a changed vector for the same target retain normal failure policy.
    row = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(document),)).fetchone()
    if row is not None:
        canonical = _decode(row[0], document)
        return (
            {m.source_ref: m.source_epoch for m in canonical.sources}
            == {s.document.source_key: s.epoch for s in sources}
            and all(m.visibility == "public" and m.public_bibliography is not None
                    and m.relation_proof_id == proof for m in canonical.sources)
        )
    successor = journal.public_job_spec(
        "relink_notices", document, input_digest=digest,
        implementation_digest=IMPLEMENTATION_DIGEST, source_ref=anchor,
    )
    pending = journal._load(db, journal._key(successor))
    return (pending is not None and pending.spec == successor
            and pending.state == "pending" and pending.attempts < journal._MAX_ATTEMPTS)


def mark_initial_group_superseded(storage, lease, *, now):
    """Recheck the positive successor and exact old lease in one writer txn."""
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        job = journal._held(db, lease, now)
        if job.spec.implementation_digest != IMPLEMENTATION_DIGEST:
            return False
        try:
            inputs = _group_inputs(db, job.spec.source_ref)
            qualifies = _superseding_group_txn(db, job.spec, inputs)
        except (ValueError, TypeError):
            return False
        if not qualifies:
            return False
        journal._save(db, replace(job, state="stale", reason="input_superseded",
                                  lease_until=0))
        return True


def prepare_initial_group_publication(storage, spec):
    journal._check_spec(spec)
    if spec.implementation_digest != IMPLEMENTATION_DIGEST:
        raise ValueError("notice_work_invalid_initial_source")

    def checked(db):
        inputs = _group_inputs(db, spec.source_ref)
        if inputs is None:
            raise ValueError("notice_work_initial_group_changed")
        document, sources, anchor, proof, digest = inputs
        if (document.source_key != spec.target or anchor != spec.source_ref
                or digest != spec.input_digest):
            if _superseding_group_txn(db, spec, inputs):
                raise InitialGroupSuperseded("notice_work_initial_group_superseded")
            raise ValueError("notice_work_initial_group_changed")
        return document, sources, proof

    def check_members(canonical, document, sources, proof):
        expected = {source.document.source_key: source.epoch for source in sources}
        if (canonical.document != document
                or {member.source_ref: member.source_epoch for member in canonical.sources} != expected
                or any(member.visibility != "public" or member.public_bibliography is None
                       or member.relation_proof_id != proof for member in canonical.sources)):
            raise ValueError("notice_work_invalid_initial_group")

    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        checked(db)

    def publish(db):
        document, sources, proof = checked(db)
        old = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(document),)).fetchone()
        if old is not None:
            # A concurrent normal producer may have completed exactly this
            # group. No overwrite, epoch change or notification on a no-op.
            check_members(_decode(old[0], document), document, sources, proof)
            return True
        canonical = _persist_origin_linked_public_cover_group_txn(
            db, sources=sources, expected_document=document,
        )
        check_members(canonical, document, sources, proof)
        return True

    return publish
