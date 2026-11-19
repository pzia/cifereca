"""Group publication or durable repair on the owning Search source transaction.

No independent graph authority, queue, I/O or scheduler. The caller owns the
transaction and must roll it back if a qualified repair cannot be recorded.
"""
from __future__ import annotations

import sqlite3

from . import notice_c_job_journal as journal
from .notice_c_cover_index import (
    _db_key, _decode, canonical_cover_document_ref,
    _resolve_origin_linked_public_group_txn,
    _persist_origin_linked_public_cover_group_txn,
)
from .notice_c_origin_link_producer import (
    _public_source_coordinates, _purchase_coordinates, _admitted_public_sources_txn,
    _eligible_origin_groups, _MAX_ORIGINS,
)
from .notice_c_initial_group_publication import request_search_group_repair_txn


def _request_existing_group(db, document, sources, *, now):
    # Same vector and implementation as the established exact-group worker.
    from .notice_c_aggregate_inputs import _capture_txn
    from .notice_c_origin_recompute import _group_members, IMPLEMENTATION_DIGEST
    inputs = _capture_txn(db, document)
    members = _group_members(inputs)
    if {source.document.source_key: source for source in inputs.sources} != {
            source.document.source_key: source for source in sources}:
        raise ValueError("notice_work_group_inputs_changed")
    actual = {member.source_ref: member.source_epoch for member in members}
    wanted = {source.document.source_key: source.epoch for source in sources}
    if actual == wanted:
        return None
    spec = journal.public_job_spec(
        "recompute_aggregate", document, input_digest=inputs.input_digest,
        implementation_digest=IMPLEMENTATION_DIGEST,
    )
    previous = journal._load(db, journal._key(spec))
    return journal._request_job_txn(
        db, spec, now=now, expected_generation=previous.ref.generation if previous else 0,
    )


def publish_search_groups_txn(db, currents, *, now, _refused_refs=None):
    """Return (linked source refs, exact durable repair refs), before commit.

    Discover eligible groups once for the accepted chunk. A failed writer
    rolls back its partial group before requesting repair. Unqualified
    overlaps are not admitted and must not manufacture a repair job.
    A caller that supplies a refusal set must roll back the whole snapshot
    before reattempting any independent rows. No partial repair is retained.
    """
    if not db.in_transaction:
        raise ValueError("notice_work_transaction_required")
    if (type(currents) is not tuple or len(currents) > 128
            or not journal._integer(now)):
        raise ValueError("notice_work_invalid_group_batch")
    groups, current_by_coords = _search_groups_txn(db, currents)
    linked, requests = {}, []
    for ordered, sources in groups:
        try:
            target, refs, proof = _resolve_origin_linked_public_group_txn(db, sources=sources)
        except (ValueError, TypeError):
            # Conflict is not proof of an admissible group; keep the self-C only.
            continue
        document = canonical_cover_document_ref(target)
        row = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(document),)).fetchone()
        old = None if row is None else _decode(row[0], document)
        db.execute("SAVEPOINT notice_c_group_publication")
        try:
            complete = _persist_origin_linked_public_cover_group_txn(
                db, sources=sources, expected_document=document,
            )
            if (complete.document != document
                    or {m.source_ref: m.source_epoch for m in complete.sources}
                    != {s.document.source_key: s.epoch for s in sources}
                    or any(m.visibility != "public" or m.relation_proof_id != proof
                           or m.public_bibliography is None for m in complete.sources)):
                raise ValueError("notice_work_invalid_group_publication")
        except (ValueError, TypeError, OSError, sqlite3.Error):
            db.execute("ROLLBACK TO SAVEPOINT notice_c_group_publication")
            complete = None
        finally:
            db.execute("RELEASE SAVEPOINT notice_c_group_publication")
        if complete is not None:
            for coord in ordered:
                if coord in current_by_coords:
                    linked[current_by_coords[coord].document.source_key] = complete
            continue
        # The entire eligible group has been rolled back; select initial,
        # strict extension or exact recomputation, never an invented target.
        db.execute("SAVEPOINT notice_c_group_repair")
        try:
            if old is None or {member.source_ref for member in old.sources} < refs:
                request = request_search_group_repair_txn(db, sources[0], now=now)
                if request is None:
                    raise ValueError("notice_work_group_repair_refused")
            else:
                request = _request_existing_group(db, document, sources, now=now)
            if request is not None:
                requests.append(request)
        except ValueError:
            db.execute("ROLLBACK TO SAVEPOINT notice_c_group_repair")
            if _refused_refs is None:
                raise
            # Every admitted row in the refused group must be excluded
            # together. Other groups can report their own exact refusals.
            _refused_refs.update(refs)
        finally:
            db.execute("RELEASE SAVEPOINT notice_c_group_repair")
    return linked, tuple(requests)


def _search_groups_txn(db, currents):
    """One relation discovery shared by the writer and read-only rebinding."""
    # Inputs are already accepted Core snapshots, not external bodies.
    # A large federated result may contain many unrelated catalogue rows.
    # Keep only coordinates first; match the actual current source after
    # inspecting the bounded relation graph, not the last duplicate row.
    coordinates = {coord for source in currents
                   if (coord := _public_source_coordinates(source)) is not None}
    if not coordinates:
        return (), {}
    from .library_projection import _read_provider_origin_coordinates
    verified = _read_provider_origin_coordinates(db, identity_id=None, with_kind=True)
    if not verified or len(verified) > _MAX_ORIGINS:
        return (), {}
    wanted = _purchase_coordinates(verified)
    if not (wanted & coordinates):
        return (), {}
    public = _admitted_public_sources_txn(db, wanted)
    # The resulting map is bounded by the existing origin graph ceiling;
    # an older duplicate cannot hide a later/earlier exact current receipt.
    current_by_coords = {
        coord: source for source in currents
        if (coord := _public_source_coordinates(source)) in public
        and public[coord] == source
    }
    return _eligible_origin_groups(verified, public, current_by_coords), current_by_coords


def read_committed_search_groups(storage, currents):
    """Rebind already accepted Search snapshots in one read transaction.

    Search can commit multiple writer chunks of 128 observations. The old
    live writer's 256-row limit is not a bound on this read-only projection:
    the relation scan and admitted group sizes remain independently bounded.
    """
    if type(currents) is not tuple or not currents:
        return {}
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        groups, current_by_coords = _search_groups_txn(db, currents)
        linked = {}
        for ordered, sources in groups:
            try:
                target, refs, proof = _resolve_origin_linked_public_group_txn(
                    db, sources=sources,
                )
                document = canonical_cover_document_ref(target)
                row = db.execute("SELECT value FROM metadata WHERE key=?",
                                 (_db_key(document),)).fetchone()
                if row is None:
                    continue
                complete = _decode(row[0], document)
                if (complete.document != document
                        or {m.source_ref: m.source_epoch for m in complete.sources}
                        != {s.document.source_key: s.epoch for s in sources}
                        or any(m.visibility != "public" or m.relation_proof_id != proof
                               or m.public_bibliography is None for m in complete.sources)):
                    continue
            except (ValueError, TypeError):
                continue
            for coord in ordered:
                if coord in current_by_coords:
                    linked[current_by_coords[coord].document.source_key] = complete
        return linked
