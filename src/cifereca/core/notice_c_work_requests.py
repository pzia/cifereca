"""#4982: targeted producer-side dirty-C requests, not a scheduler.

Call only after an admitted public source write on the SAME Storage
transaction, or from the explicit worker reconciliation entry point.
No new queue/index, provider request, GTK callback or automatic caller.
Only already established exact self and OriginLink documents qualify.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import notice_c_job_journal as journal
from . import notice_c_exact_recompute as exact
from . import notice_c_origin_recompute as origin
from .notice_c_aggregate_inputs import _capture_txn
from .notice_c_cover_index import (
    _PREFIX, _json, _db_key, _decode, canonical_cover_document_ref,
)
from .notice_c_origin_link_producer import reattest_exact_origin_group_txn
from .notice_cover_repository import (
    PersistedNoticeCover, _checked_source_state, _opaque_key,
    _persist_notice_cover_txn, _MAX_EPOCH,
)

from .notice_cover_projection import project_notice_cover_attribute

_MAX_MATCHES = 32


@dataclass(frozen=True, slots=True, repr=False)
class SourceChangeRequests:
    refs: tuple[journal.JobRef, ...]
    examined: int
    refused: int
    overflow: bool = False


def request_affected_public_recompute_txn(db, source, *, now, _include_groups=True):
    """Queue only dirty, currently reattested exact-source dependents.

    The supplied source must still be the actual admitted SQLite row.
    Candidate lookup is exact source DocumentRef, never title or ISBN.
    Refuse a truncated candidate set and competing multi-source relations.
    A capacity refusal is explicit; already-valid source evidence survives
    if the caller commits, so a later reconciliation may retry the request.
    Other SQLite errors propagate and roll back with the caller's writer.
    """
    if not db.in_transaction:
        raise ValueError("notice_work_transaction_required")
    if not journal._integer(now):
        raise ValueError("notice_work_invalid_clock")
    if (type(source) is not PersistedNoticeCover
            or source.observation.key.visibility != "public"
            or source.document.kind != "public_catalogue"
            or source.observation.parser_id != "core.search.public-cover"
            or source.observation.parser_version != "2"):
        raise ValueError("notice_work_public_source_required")
    key = _opaque_key(source.document)
    if key is None:
        raise ValueError("notice_work_public_source_required")
    _, current, _ = _checked_source_state(db, source.document, key)
    if current != source:
        raise ValueError("notice_work_source_changed")

    rows = db.execute(
        "SELECT m.key,m.value FROM metadata AS m "
        "WHERE m.key>=? AND m.key<? AND EXISTS ("
        "SELECT 1 FROM json_each(CASE WHEN json_valid(m.value) "
        "THEN m.value ELSE '{}' END, '$.sources') src WHERE src.key=?) "
        "ORDER BY m.key LIMIT ?",
        (_PREFIX, _PREFIX + "\uffff", source.document.source_key, _MAX_MATCHES + 1),
    ).fetchall()
    if len(rows) > _MAX_MATCHES:
        return SourceChangeRequests((), _MAX_MATCHES, 0, True)

    candidates = []
    refused = 0
    multi = 0
    uncertain = False
    for row_key, raw in rows:
        try:
            document = canonical_cover_document_ref(_json(raw)["edition_id"])
            if _db_key(document) != row_key:
                raise ValueError("notice_work_corrupt_candidate")
            canonical = _decode(raw, document)
            member, = (m for m in canonical.sources
                       if m.source_ref == source.document.source_key)
            is_multi = len(canonical.sources) > 1
            if is_multi and not _include_groups:
                # The chunk group writer owns relation repair in this mode.
                continue
            multi += int(is_multi)
            if (member.provider_id != source.observation.key.provider_id
                    or member.visibility != "public"):
                raise ValueError("notice_work_corrupt_candidate")
            # A fresh watermark alone is not a new documentary revision.
            # A dirty C remains dirty even if its current request predates
            # a watermark update; capturing the newest vector supersedes it.
            if member.source_epoch == source.epoch:
                # Another member may be stale; this event only concerns
                # this exact source. Do not create no-op background work.
                continue
            if member.source_epoch > source.epoch:
                raise ValueError("notice_work_source_changed")
            candidates.append((document, is_multi))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            refused += 1
            uncertain = True

    refs = []
    for document, is_multi in candidates:
        if is_multi and (multi != 1 or uncertain):
            refused += 1
            continue
        try:
            inputs = _capture_txn(db, document)
            if is_multi:
                origin._group_members(inputs)
                reattest_exact_origin_group_txn(db, inputs.sources)
                implementation = origin.IMPLEMENTATION_DIGEST
            else:
                exact._exact_member(inputs)
                implementation = exact.IMPLEMENTATION_DIGEST
            spec = journal.public_job_spec(
                "recompute_aggregate", document, input_digest=inputs.input_digest,
                implementation_digest=implementation,
            )
            previous = journal._load(db, journal._key(spec))
            generation = previous.ref.generation if previous else 0
            refs.append(journal._request_job_txn(
                db, spec, now=now, expected_generation=generation,
            ))
        except ValueError:
            # Invalid/missing source, stale or ambiguous evidence, corrupt
            # job, capacity. A closed count only; no private data in output.
            refused += 1
    return SourceChangeRequests(tuple(refs), len(rows), refused)


def request_source_worker_dependents_txn(db, source, *, now):
    """Compose all qualified documentary repair intents for a source worker.

    Fetch and cache-only replay already hold their exact input/lease guards.
    This port only records work; no inline canonical publication, transport,
    or scheduler. No eligible live group means the original dependent scan
    retains its refusal semantics for dirty, unqualified retained groups.
    """
    if (not db.in_transaction or not journal._integer(now)
            or type(source) is not PersistedNoticeCover):
        raise ValueError("notice_work_invalid_source_worker")
    from .notice_c_initial_group_publication import (
        _group_inputs, request_search_group_repair_txn,
    )
    from .notice_c_group_batch_publication import _request_existing_group

    if source.observation.key.visibility != "public":
        raise ValueError("notice_work_public_source_required")
    inputs = _group_inputs(db, source.document.source_key)
    if inputs is None:
        return request_affected_public_recompute_txn(db, source, now=now)
    document, sources, _anchor, _proof, _digest = inputs
    if source not in sources:
        raise ValueError("notice_work_source_changed")
    # The group resolver above already checked every competing C overlap.
    # Keep the exact self-C scan and capacity/ambiguity accounting, but do
    # not demand an exact-membership recompute for a strict extension.
    dirty = request_affected_public_recompute_txn(
        db, source, now=now, _include_groups=False,
    )
    if dirty.refused or dirty.overflow:
        return dirty
    row = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(document),)).fetchone()
    refs = {member.document.source_key for member in sources}
    if row is None or {member.source_ref for member in _decode(row[0], document).sources} < refs:
        group = request_search_group_repair_txn(db, source, now=now)
        if group is None:
            raise ValueError("notice_work_group_repair_refused")
    else:
        # The full group, not just the changed member, is dirty or current.
        # This also renews its vector after a freshness-only source update
        # while another member still awaits a documentary recomposition.
        group = _request_existing_group(db, document, sources, now=now)
    return SourceChangeRequests(
        dirty.refs + (() if group is None else (group,)),
        dirty.examined, dirty.refused, dirty.overflow,
    )


def request_affected_public_recompute(storage, source, *, now):
    """Explicit worker reconciliation; no producer or pump auto-activation."""
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return request_affected_public_recompute_txn(db, source, now=now)


def persist_public_source_and_request(
    storage, observation, *, field_path, cover_inventory_complete,
    now, expected_epoch=None,
):
    """Opt-in producer port: source publication + dirty requests, atomic.

    Reuse the canonical source writer. No existing producer is switched
    automatically; the coordinator must explicitly call this off GTK.
    If a partial write is declined by the source writer, no request follows.
    """
    if (observation.key.visibility != "public"
            or observation.parser_id != "core.search.public-cover"
            or observation.parser_version != "2"):
        raise ValueError("notice_work_public_source_required")
    if (expected_epoch is not None
            and (type(expected_epoch) is not int
                 or not 0 <= expected_epoch < _MAX_EPOCH)):
        raise ValueError("notice_c_persisted_epoch_invalid")
    if not journal._integer(now):
        raise ValueError("notice_work_invalid_clock")
    attribute = project_notice_cover_attribute(
        observation, field_path=field_path,
        cover_inventory_complete=cover_inventory_complete,
    )
    key = _opaque_key(attribute.document)
    if key is None:
        raise ValueError("notice_c_persisted_scope_invalid")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        published = _persist_notice_cover_txn(
            db, observation, attribute, key, field_path,
            cover_inventory_complete, expected_epoch,
        )
        if published is None:
            return published, SourceChangeRequests((), 0, 0)
        return published, request_affected_public_recompute_txn(
            db, published, now=now,
        )
