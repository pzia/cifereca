"""First exact public self-C publication via the existing durable work journal.

A source coordinate is not a relation proof. Re-read the independently
admitted raw source and its fence, rederive its exact self edition and CAS
the absence of C in the publication transaction. No GET or group creation.
"""
from __future__ import annotations

from hashlib import sha256
import json

from . import notice_c_job_journal as journal
from .notice_c_cover_index import (
    canonical_cover_document_ref, _db_key, _exact_public_search_evidence,
    _persist_exact_public_search_cover_txn,
)
from .notice_c_aggregate_inputs import _capture_txn
from .notice_c_exact_recompute import _exact_member
from .notice_c_public_search import _public_observation
from .notice_cover_repository import _PREFIX, _checked_source_state, PersistedNoticeCover

IMPLEMENTATION_DIGEST = journal._INITIAL_PUBLIC_C_DIGEST


def _inputs(db, source_ref):
    # Caller validated the source coordinate through the closed job schema
    # or derived it from an admitted PersistedNoticeCover.
    if not db.in_transaction:
        raise ValueError("notice_work_transaction_required")
    if (type(source_ref) is not str or not source_ref.startswith("notice-c:v1:")
            or not journal._digest(source_ref[len("notice-c:v1:"):])):
        raise ValueError("notice_work_invalid_initial_source")
    key = _PREFIX + source_ref[len("notice-c:v1:"):]
    row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    hit = None if row is None else _public_observation(row[0], key_string=key, now=0)
    if hit is None or hit.document.source_key != source_ref:
        raise ValueError("notice_work_initial_source_unavailable")
    raw, source, _fence = _checked_source_state(db, hit.document, key)
    edition, _proof = _exact_public_search_evidence(source)
    document = canonical_cover_document_ref(edition)
    # Freshness-only re-observation does not invalidate a documentary job.
    payload = json.loads(raw[0])
    payload.pop("last_seen_at", None)
    wire = json.dumps(
        ["cifereca.notice-c.initial-input.v1", source_ref, document.source_key, payload],
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )
    return source, document, sha256(wire.encode("ascii")).hexdigest()


def request_initial_publication(storage, source, *, now):
    """Only failed live exact self-C creation; never replace an existing C."""
    if type(source) is not PersistedNoticeCover or not journal._integer(now):
        raise ValueError("notice_work_invalid_initial_source")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return request_initial_publication_txn(db, source, now=now)


def request_initial_publication_txn(db, source, *, now):
    """Compose source and repair intent on the canonical writer transaction."""
    if type(source) is not PersistedNoticeCover or not journal._integer(now):
        raise ValueError("notice_work_invalid_initial_source")
    current, document, digest = _inputs(db, source.document.source_key)
    if current != source:
        raise ValueError("notice_work_source_changed")
    if db.execute("SELECT 1 FROM metadata WHERE key=?", (_db_key(document),)).fetchone():
        return None
    spec = journal.public_job_spec(
        "recompute_aggregate", document, input_digest=digest,
        implementation_digest=IMPLEMENTATION_DIGEST,
        source_ref=source.document.source_key,
    )
    previous = journal._load(db, journal._key(spec))
    return journal._request_job_txn(
        db, spec, now=now, expected_generation=previous.ref.generation if previous else 0,
    )


def prepare_initial_publication(storage, spec):
    journal._check_spec(spec)
    if spec.implementation_digest != IMPLEMENTATION_DIGEST:
        raise ValueError("notice_work_invalid_initial_source")

    def checked(db):
        source, document, digest = _inputs(db, spec.source_ref)
        if document.source_key != spec.target or digest != spec.input_digest:
            raise ValueError("notice_work_initial_source_changed")
        return source, document

    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        checked(db)

    def publish(db):
        source, document = checked(db)
        if db.execute("SELECT 1 FROM metadata WHERE key=?", (_db_key(document),)).fetchone():
            # A competing normal producer may already have published exactly
            # this observation. Acknowledge it without overwriting or notifying.
            inputs = _capture_txn(db, document)
            if (_exact_member(inputs) != source
                    or inputs.canonical.sources[0].source_epoch != source.epoch):
                raise ValueError("notice_work_initial_c_changed")
            return True
        published = _persist_exact_public_search_cover_txn(db, source, expected_epoch=0)
        if (published.document != document or len(published.sources) != 1
                or published.sources[0].source_epoch != source.epoch
                or published.sources[0].public_bibliography is None):
            raise ValueError("notice_work_invalid_initial_publication")
        return True

    return publish
