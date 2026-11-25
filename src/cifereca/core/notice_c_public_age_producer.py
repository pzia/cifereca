"""Trusted worker producer for current explicitly scoped public-age journal generations.

Scope applicability is explicitly supplied by the Core owner. This is not
a read-triggered refresh, scope inference, queue, scheduler or plugin API.
The current generation is derived atomically, not from a view or cache.
"""
from . import notice_c_job_journal as journal
from .notice_c_cover_index import canonical_cover_document_ref, is_canonical_cover_document
from .notice_c_public_age_request import _snapshot
from .notice_consolidator_selection import _identifier


def request_current_public_age_txn(db, *, target_edition_id, scope, now,
                                   implementation_digests, group_document=None):
    """Create/deduplicate the current explicitly located job inside the caller transaction.

    Do not reopen a terminal job on identical inputs. The normal journal
    rules govern supersession, capacity, and durable generation allocation.
    The admitted worker independently checks archive authority and lineage.
    """
    if not db.in_transaction:
        raise ValueError("notice_work_transaction_required")
    if not journal._integer(now):
        raise ValueError("notice_work_invalid_clock")
    canonical_cover_document_ref(target_edition_id)
    if not _identifier(scope, max_len=160):
        raise ValueError("notice_descriptive_invalid_scope")
    if (type(implementation_digests) is not tuple
            or not 1 <= len(implementation_digests) <= 128
            or any(not journal._digest(value) for value in implementation_digests)
            or len(set(implementation_digests)) != len(implementation_digests)):
        raise ValueError("notice_work_invalid_public_age_filter")
    if group_document is not None and not is_canonical_cover_document(group_document):
        raise ValueError("notice_work_invalid_descriptive_spec")
    snapshot = _snapshot(db, target_edition_id=target_edition_id, scope=scope,
                         group_document=group_document)
    if snapshot is None:
        return None
    spec = snapshot[0]
    if spec.implementation_digest not in implementation_digests:
        return None
    previous = journal._load(db, journal._key(spec))
    # Neither exact nor group publication may silently replace another
    # locator. The scope owner must arbitrate that change by explicit CAS.
    if previous is not None and previous.spec.public_age_locator != spec.public_age_locator:
        return None
    return journal._request_job_txn(
        db, spec, now=now, expected_generation=previous.ref.generation if previous else 0,
    )


def request_current_public_age(storage, *, target_edition_id, scope, now,
                               implementation_digests, group_document=None):
    """Worker-only transaction wrapper; no submission or transport."""
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return request_current_public_age_txn(
            db, target_edition_id=target_edition_id, scope=scope, now=now,
            implementation_digests=implementation_digests, group_document=group_document,
        )
