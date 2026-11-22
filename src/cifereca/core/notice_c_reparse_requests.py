"""#4982: inert public reparse journal binding, no worker or plugin dispatch.

Only an explicit admitted producer may request a job. Its receipt must
identify an independently retained exact input. A successful journal
request is not proof that the cache entry remains available at execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json

from . import notice_c_job_journal as journal
from . import notice_c_raw_input_receipt as raw
from .notice_cover_projection import notice_cover_document_ref
from .notice_cover_repository import (
    _PREFIX as _SOURCE_PREFIX, _MAX_BYTES as _MAX_SOURCE_BYTES,
    _unique_object, _decode, PersistedNoticeCover,
)
from .notice_source_observation import NoticeSourceKey


@dataclass(frozen=True, slots=True, repr=False)
class PublicReparseRequest:
    source: PersistedNoticeCover
    receipt: raw.PublicRawInputReceipt
    implementation_digest: str


def _spec(source, receipt, implementation_digest):
    if not raw._valid(receipt) or receipt.producer_token is None:
        raise ValueError("notice_reparse_input_not_locatable")
    # Store only a hash in the durable journal. The token itself has the
    # shorter receipt TTL and is never a transport URL or capability.
    encoded = json.dumps(
        ["cifereca.notice-c.reparse-input.v1",
         source.document.source_key, raw._encode(receipt)],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("ascii")
    return journal.public_job_spec(
        "reparse_cached_input", source.document,
        input_digest=sha256(encoded).hexdigest(),
        implementation_digest=implementation_digest,
    )


def request_public_reparse(storage, source, receipt, body, *,
                           implementation_digest, now, expected_generation):
    """Compatibility entry point through the common typed Core admission."""
    from .notice_c_enrichment_intent import CatalogueReparseIntent, request_notice_enrichment
    admission = request_notice_enrichment(
        storage, CatalogueReparseIntent(source, receipt, body, implementation_digest,
                                        expected_generation), now=now,
    )
    return admission.refs[0]


def _request_public_reparse(storage, source, receipt, body, *,
                            implementation_digest, now, expected_generation):
    """Request a cache-only parser version, atomically with its input CAS.

    Caller is a trusted Core producer, never an arbitrary plugin or GTK
    cache hit. It must verify the real retained bytes and selected parser
    registration separately. No GET/fetch dependency is inserted.
    """
    if type(source) is not PersistedNoticeCover:
        raise ValueError("notice_reparse_invalid_source")
    spec = _spec(source, receipt, implementation_digest)
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = raw.check_public_raw_input_txn(
            db, source.document, receipt, body, now=now,
        )
        if current != source:
            raise ValueError("notice_reparse_source_changed")
        return journal._request_job_txn(
            db, spec, now=now, expected_generation=expected_generation,
        )


def _document_for_target(db, target):
    # The journal deliberately contains only the opaque source ref, not
    # provider/account coordinates. Recover the document from its actual
    # admitted row, then verify the whole row and the hash coordinate.
    key = _SOURCE_PREFIX + target[len("notice-c:v1:"):]
    row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    if row is None:
        raise ValueError("notice_reparse_source_missing")
    value = row[0]
    try:
        if type(value) is not str or not 1 <= len(value.encode("utf-8")) <= _MAX_SOURCE_BYTES:
            raise ValueError
        data = json.loads(value, object_pairs_hook=_unique_object)
        coordinates = NoticeSourceKey(**data["key"])
        if coordinates.visibility != "public":
            raise ValueError
        document = notice_cover_document_ref(coordinates)
        if document.source_key != target:
            raise ValueError
        _decode(value, document)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError,
            RecursionError, OverflowError) as exc:
        raise ValueError("notice_reparse_invalid_source") from exc
    return document


def read_public_reparse_request_txn(db, spec, *, now):
    """Resolve the exact receipt for an already recorded candidate job.

    This does not load the supplier cache or authorize its parser. Run on
    an admitted Core worker; no private coordinate may escape a failed read.
    """
    if not db.in_transaction:
        raise ValueError("notice_reparse_transaction_required")
    raw._clock(now)
    journal._check_spec(spec)
    if spec.kind != "reparse_cached_input" or spec.dependencies:
        raise ValueError("notice_reparse_unsupported_spec")
    document = _document_for_target(db, spec.target)
    receipt = raw._read(db, raw._key(document), now)
    if receipt is None or receipt.producer_token is None:
        raise ValueError("notice_reparse_input_unavailable")
    source, state = raw._source_state(db, document)
    if (source is None or state != receipt.source_state_digest
            or raw.admitted_source_scope_sha256(
                source.observation.key, parser_id=source.observation.parser_id,
            ) != receipt.source_scope_digest):
        raise ValueError("notice_reparse_source_changed")
    if _spec(source, receipt, spec.implementation_digest) != spec:
        raise ValueError("notice_reparse_inputs_changed")
    return PublicReparseRequest(source, receipt, spec.implementation_digest)


def read_public_reparse_request(storage, spec, *, now):
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        return read_public_reparse_request_txn(db, spec, now=now)


def check_public_reparse_inputs_txn(db, spec, prepared, body, *,
                                  producer_token, transport_digest,
                                  parser_family_digest, implementation_digest,
                                  now):
    """Revalidate the specific raw input *and* selected replay implementation.

    The future executor must also hold the journal lease and an actual live
    producer cache/session lease. Only the current trusted registry may
    supply implementation_digest; a plugin's self-reported hash is not
    authority. This port performs no write and grants no parser capability.
    """
    current = read_public_reparse_request_txn(db, spec, now=now)
    if type(prepared) is not PublicReparseRequest or prepared != current:
        raise ValueError("notice_reparse_inputs_changed")
    receipt = current.receipt
    if (producer_token != receipt.producer_token
            or transport_digest != receipt.transport_digest
            or parser_family_digest != receipt.parser_family_digest
            or implementation_digest != current.implementation_digest):
        raise ValueError("notice_reparse_implementation_or_transport_changed")
    return raw.check_public_raw_input_txn(
        db, current.source.document, receipt, body, now=now,
    )
