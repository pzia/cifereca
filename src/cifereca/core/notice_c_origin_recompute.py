"""Public multi-source C recomposition on fresh Core OriginLink evidence.

No relink/extension, supplier request, private grant or scheduler activation.
The public aggregate vector is durable; private OriginLink facts are read
only as a live veto and never serialized into the job or the canonical C.
"""
from __future__ import annotations

from hashlib import sha256

from .book_cover_projection import CoverDocumentScope
from .notice_c_aggregate_inputs import (
    _capture_txn, check_public_aggregate_inputs_txn,
)
from .notice_c_cover_index import (
    canonical_cover_document_ref, _persist_origin_linked_public_cover_group_txn,
)
from .notice_c_origin_link_producer import reattest_exact_origin_group_txn
from . import notice_c_job_journal as journal

IMPLEMENTATION_DIGEST = sha256(b"core.origin-linked-public-c-recompute.v1").hexdigest()
_PROOF_PREFIX = "core.origin.linked.v1:"


def _group_members(inputs):
    if inputs is None or len(inputs.sources) < 2:
        raise ValueError("notice_recompute_origin_group_required")
    members = inputs.canonical.sources
    proofs = {member.relation_proof_id for member in members}
    if (len(proofs) != 1 or len(members) != len(inputs.sources)
            or any(member.visibility != "public"
                   or member.public_bibliography is None for member in members)):
        raise ValueError("notice_recompute_origin_group_required")
    proof, = proofs
    suffix = proof[len(_PROOF_PREFIX):] if proof.startswith(_PROOF_PREFIX) else ""
    if (len(suffix) != 64 or any(c not in "0123456789abcdef" for c in suffix)
            or canonical_cover_document_ref("edition:origin-linked:v1:" + suffix)
            != inputs.canonical.document):
        raise ValueError("notice_recompute_origin_group_required")
    if {member.source_ref for member in members} != {
        source.document.source_key for source in inputs.sources
    }:
        raise ValueError("notice_recompute_origin_group_required")
    return members


def _capture_reattested(storage, document):
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        inputs = _capture_txn(db, document)
        _group_members(inputs)
        reattest_exact_origin_group_txn(db, inputs.sources)
        return inputs


def request_origin_public_recompute(storage, document, *, now, expected_generation):
    """Explicit trusted request; a current proof is required even to enqueue."""
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        inputs = _capture_txn(db, document)
        _group_members(inputs)
        reattest_exact_origin_group_txn(db, inputs.sources)
        spec = journal.public_job_spec(
            "recompute_aggregate", document, input_digest=inputs.input_digest,
            implementation_digest=IMPLEMENTATION_DIGEST,
        )
        return journal._request_job_txn(db, spec, now=now,
                                        expected_generation=expected_generation)


def prepare_origin_public_recompute(storage, spec):
    """Prepare a publication closure, but do not publish or consume a lease."""
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
    inputs = _capture_reattested(storage, document)
    if inputs.input_digest != spec.input_digest:
        raise ValueError("notice_aggregate_inputs_changed")

    def publish(db):
        current = check_public_aggregate_inputs_txn(
            db, document, expected_digest=spec.input_digest,
        )
        members = _group_members(current)
        sources = reattest_exact_origin_group_txn(db, current.sources)
        published = _persist_origin_linked_public_cover_group_txn(
            db, sources=sources, expected_document=document,
        )
        old_proofs = {member.source_ref: member.relation_proof_id
                      for member in members}
        new_proofs = {member.source_ref: member.relation_proof_id
                      for member in published.sources}
        expected_epochs = {source.document.source_key: source.epoch
                           for source in sources}
        if (published.document != document or new_proofs != old_proofs
                or {member.source_ref: member.source_epoch
                    for member in published.sources} != expected_epochs):
            raise ValueError("notice_recompute_invalid_publication")
        return True

    return publish
