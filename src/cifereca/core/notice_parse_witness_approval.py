"""Q5: explicit public Search witness approval on the existing source/receipt CAS.

No automatic pin, provider GET, raw export, or second witness store.
"""
from dataclasses import dataclass
from time import time

from ..plugin_api import PublicSearchReplayCapability, PublicSearchReplayInput
from ..models import SearchResult
from .notice_c_public_search_replay import _anonymous_session
from .notice_c_raw_input_receipt import read_public_raw_input_receipt, check_public_raw_input_txn
from .notice_cover_repository import read_notice_cover
from .notice_parse_witness import CriticalParseField, field_digest
from .notice_parse_witness_store import (
    read_approved_parse_witness, _prepare_approved_parse_witness,
    _publish_approved_parse_witness_txn,
)
from .search_orchestration import SearchReadScope, _verified_public_cover_witness, _CORE_PUBLIC_COVER_WITNESS


@dataclass(frozen=True, slots=True, repr=False)
class PublicWitnessProposal:
    document: object
    source_epoch: int
    receipt: object
    generation: int
    fields: tuple[CriticalParseField, ...]


def public_search_witness(service, row, *, scope, approved=None, now=None):
    """Read a plugin proposal, or CAS the exact explicitly approved proposal.

    Called on an existing Core worker. The approval object contains only
    coordinates, digests and fields, never retained response bytes.
    """
    if (type(row) is not SearchResult or type(scope) is not SearchReadScope
            or (approved is not None and type(approved) is not PublicWitnessProposal)
            or not _verified_public_cover_witness(row)):
        return None
    clock = (lambda: int(time())) if now is None else (lambda: now)
    document = getattr(row, "_core_notice_cover_document_ref", None)
    if document is None or document.kind != "public_catalogue":
        return None
    with service._source_context_lock:
        if (not scope.current(service) or service.current_identity is not scope.identity
                or service._provider_enabled_for_identity(document.provider_id, scope.identity_id) is not True):
            return None
        session = service._sessions.get(service._session_key(document.provider_id, "__public__"))
        if not _anonymous_session(session, document.provider_id):
            return None
        source = read_notice_cover(service.storage, document)
        receipt = read_public_raw_input_receipt(service.storage, document, now=clock())
        if (source is None or receipt is None or receipt.producer_token is None
                or source.observation.status != "complete"
                or source.epoch != getattr(row, "_core_notice_cover_document_epoch", None)):
            return None
        evidence = getattr(row, "_core_notice_parse_input", None)
        if (type(evidence) is not tuple or evidence != (
                _CORE_PUBLIC_COVER_WITNESS, source.observation.key,
                source.observation.raw_json, source.observation.parser_id,
                source.observation.parser_version, receipt.input_digest,
                receipt.parser_implementation_digest)):
            return None
        cap = session.public_search_replay_capability()
        if (type(cap) is not PublicSearchReplayCapability or cap.schema_version != 1
                or cap.parser_family_digest != receipt.parser_family_digest
                or cap.parser_implementation_digest != receipt.parser_implementation_digest):
            return None
        names = cap.witness_fields
        if (type(names) is not tuple or not 1 <= len(names) <= 8
                or any(type(name) is not str or name not in (
                    "title", "record_id", "author", "format", "publisher", "docbase", "medium"
                ) for name in names) or len(set(names)) != len(names)):
            return None
        bio = source.observation.fields_copy()["search_result"]["public_bibliography"]
        if any(type(bio.get(name)) is not str or not bio[name] for name in names):
            return None
        fields = tuple(CriticalParseField(
            ("search_result", "public_bibliography", name), "string",
            expected_sha256=field_digest(bio[name]),
        ) for name in names)
        existing = read_approved_parse_witness(
            service.storage, key=source.observation.key, parser_id=source.observation.parser_id,
        )
        generation = existing.generation if existing is not None else 0
        proposal = PublicWitnessProposal(document, source.epoch, receipt, generation, fields)
        if approved is not None and proposal != approved:
            return None
        with cap.hold(receipt.producer_token) as held:
            if (type(held) is not PublicSearchReplayInput
                    or held.token != receipt.producer_token
                    or held.transport_digest != receipt.transport_digest
                    or held.parser_family_digest != receipt.parser_family_digest
                    or held.parser_implementation_digest != receipt.parser_implementation_digest
                    or not scope.current(service) or service.current_identity is not scope.identity
                    or not _anonymous_session(session, document.provider_id)):
                return None
            with service.storage._lock, service.storage._connect() as db:
                db.execute("BEGIN IMMEDIATE" if approved is not None else "BEGIN")
                current = check_public_raw_input_txn(db, document, receipt, held.body, now=clock())
                if current != source:
                    return None
                if approved is None:
                    return proposal
                key, baseline = _prepare_approved_parse_witness(
                    observation=source.observation, response_bytes=held.body,
                    critical_fields=fields, expected_generation=generation,
                    parser_implementation_sha256=receipt.parser_implementation_digest,
                )
                return _publish_approved_parse_witness_txn(
                    db, key, baseline, expected_generation=generation,
                )
