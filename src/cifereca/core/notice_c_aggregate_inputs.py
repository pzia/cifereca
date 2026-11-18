"""Exact public-C input snapshot for #4982, no scheduler or consolidation rules.

Capture the retained C and all of its live source proof generations under
one read snapshot. Recheck the SAME input vector under the eventual
publication transaction. A digest witnesses equality, not authority to
reuse an old relationship, run a parser, or dispatch supplier IO.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re

from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import (
    ConsolidatedNoticeCover, _db_key, _decode, is_canonical_cover_document,
)
from .notice_cover_repository import (
    PersistedNoticeCover, _checked_source_state, _opaque_key,
)
from .notice_source_observation import NoticeSourceObservation

_HASH = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True, repr=False)
class PublicAggregateInputs:
    canonical: ConsolidatedNoticeCover
    sources: tuple[PersistedNoticeCover, ...]
    input_digest: str


def _digest(value):
    return sha256(value.encode("utf-8")).hexdigest()


def _capture_txn(db, document):
    if not db.in_transaction:
        raise ValueError("notice_aggregate_transaction_required")
    if not is_canonical_cover_document(document):
        raise ValueError("notice_aggregate_invalid_target")
    row = db.execute(
        "SELECT value FROM metadata WHERE key=?", (_db_key(document),),
    ).fetchone()
    if row is None:
        return None
    canonical = _decode(row[0], document)
    if any(member.visibility != "public" for member in canonical.sources):
        # No private scope reconstruction from an opaque retained C.
        return None
    sources = []
    proofs = []
    for member in sorted(canonical.sources, key=lambda item: item.source_ref):
        scope = CoverDocumentScope(
            kind="public_catalogue", identity_id=None,
            provider_id=member.provider_id, credential_id="__public__",
            source_key=member.source_ref,
        )
        key = _opaque_key(scope)
        if key is None:
            raise ValueError("notice_aggregate_invalid_source")
        raw, source, fence = _checked_source_state(db, scope, key)
        if source is None:
            # A retained canonical source is not raw input to reparse or
            # to reattest a relationship. No implicit cache refill.
            return None
        if (type(source.observation) is not NoticeSourceObservation
                or source.observation.key.visibility != "public"
                or source.document != scope
                or source.epoch < member.source_epoch
                or source.observation.observed_at < member.observed_at
                or (source.epoch == member.source_epoch and
                    source.observation.source_revision != member.source_revision)):
            raise ValueError("notice_aggregate_stale_source")
        sources.append(source)
        # The exact durable C row includes membership, proof IDs, revision,
        # and alternatives. The source row includes parser, publication
        # epoch, observation watermark and projection contract. The fence
        # is checked by the repository before entering the vector.
        proofs.append((member.source_ref, _digest(raw[0]), fence))
    wire = json.dumps(
        ["cifereca.notice-c.aggregate-input.v1", document.source_key,
         _digest(row[0]), proofs],
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )
    return PublicAggregateInputs(canonical, tuple(sources), _digest(wire))


def capture_public_aggregate_inputs(storage, document):
    """Worker-only snapshot, None if a member is missing or not public.

    No source/cover computation, writes or provider requests. An ordinary
    N2 read continues to use durable C even when this work is unavailable.
    """
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        return _capture_txn(db, document)


def check_public_aggregate_inputs_txn(db, document, *, expected_digest):
    """Return current typed inputs only if the full vector is unchanged.

    Caller owns BEGIN IMMEDIATE + Storage lock. Do not use the result to
    manufacture/revive a cross-provider relationship. Actual publication
    still requires the selected implementation, fresh relation proof and
    job lease CAS, all before the same transaction commits.
    """
    if (type(expected_digest) is not str
            or _HASH.fullmatch(expected_digest) is None):
        raise ValueError("notice_aggregate_invalid_digest")
    current = _capture_txn(db, document)
    if current is None or current.input_digest != expected_digest:
        raise ValueError("notice_aggregate_inputs_changed")
    return current


__all__ = ["PublicAggregateInputs", "capture_public_aggregate_inputs",
           "check_public_aggregate_inputs_txn"]
