"""Exact self-C recomposition port for #4982; no scheduler activation.

A singleton with native Search self-identity can reattest its own edition
from its live source. Multi-provider relations require a different, fresh
OriginLink authority and are deliberately refused here. No GET or new
storage; the existing canonical writer and the aggregate vector are reused.
"""
from __future__ import annotations

from hashlib import sha256

from .book_cover_projection import CoverDocumentScope
from .notice_c_aggregate_inputs import (
    _capture_txn, capture_public_aggregate_inputs, check_public_aggregate_inputs_txn,
)
from .notice_c_cover_index import (
    canonical_cover_document_ref, _exact_public_search_evidence,
    _persist_exact_public_search_cover_txn,
)
from . import notice_c_job_journal as journal

# Version of the Core recomposition contract, not a source/parser version.
# Bump whenever its input or publication semantics change.
IMPLEMENTATION_DIGEST = sha256(b"core.exact-public-c-recompute.v1").hexdigest()


def _exact_member(inputs):
    if inputs is None or len(inputs.sources) != 1 or len(inputs.canonical.sources) != 1:
        raise ValueError("notice_recompute_exact_source_required")
    source, = inputs.sources
    member, = inputs.canonical.sources
    edition, proof = _exact_public_search_evidence(source)
    if (inputs.canonical.document != canonical_cover_document_ref(edition)
            or member.source_ref != source.document.source_key
            or member.provider_id != source.observation.key.provider_id
            or member.visibility != "public"
            or member.relation_proof_id != proof
            or member.public_bibliography is None):
        raise ValueError("notice_recompute_exact_source_required")
    return source


def request_exact_public_recompute(storage, document, *, now, expected_generation):
    """Explicit Core request, no automatic request or source fetch."""
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        inputs = _capture_txn(db, document)
        _exact_member(inputs)
        spec = journal.public_job_spec(
            "recompute_aggregate", document, input_digest=inputs.input_digest,
            implementation_digest=IMPLEMENTATION_DIGEST,
        )
        return journal._request_job_txn(db, spec, now=now,
                                        expected_generation=expected_generation)


def prepare_exact_public_recompute(storage, spec):
    """Return the trusted transactional publication port for one job.

    No closure or bibliographic copy is persisted. The executor must claim
    and recheck the job lease, run this port on its BEGIN IMMEDIATE and mark
    the journal current in the SAME transaction. The port cannot commit.
    No automatic retries or supplier requests originate from this module.
    """
    journal._check_spec(spec)
    if (spec.kind != "recompute_aggregate"
            or spec.implementation_digest != IMPLEMENTATION_DIGEST
            or spec.dependencies):
        raise ValueError("notice_recompute_unsupported_spec")
    document = CoverDocumentScope(
        kind="public_catalogue", identity_id=None,
        source_key=spec.target, provider_id="cifereca.c",
        credential_id="__public__",
    )
    inputs = capture_public_aggregate_inputs(storage, document)
    _exact_member(inputs)
    if inputs.input_digest != spec.input_digest:
        raise ValueError("notice_aggregate_inputs_changed")

    def publish(db):
        current = check_public_aggregate_inputs_txn(
            db, document, expected_digest=spec.input_digest,
        )
        source = _exact_member(current)
        published = _persist_exact_public_search_cover_txn(
            db, source, expected_epoch=current.canonical.epoch,
        )
        if (published.document != document or len(published.sources) != 1
                or published.sources[0].source_epoch != source.epoch
                or published.sources[0].public_bibliography is None):
            raise ValueError("notice_recompute_invalid_publication")
        return True

    return publish
