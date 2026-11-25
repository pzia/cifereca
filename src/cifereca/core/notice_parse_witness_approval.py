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


def public_search_witness_diagnostic(service, row, *, scope):
    """Inspect the exact selected public source, even if its new parse failed.

    No receipt freshness is needed to inspect a durable alert. The same-call
    row must still attest the exact public source, and current reader policy
    must allow it. No private pin, raw bytes or capture path is exposed.
    """
    from .notice_search_cover_producer import admit_public_search_catalogue
    from .notice_cover_projection import notice_cover_document_ref
    from .notice_parse_witness_alert import read_parse_witness_diagnostic

    if (type(row) is not SearchResult or type(scope) is not SearchReadScope
            or not _verified_public_cover_witness(row)):
        return None
    try:
        observation = admit_public_search_catalogue(
            row, identity_id="__public__", observed_at=1,
            public_cover_url=row.thumbnail_url,
        )
        evidence = getattr(row, "_core_notice_parse_input", None)
        if (observation is None or observation.key.visibility != "public"
                or type(evidence) is not tuple or len(evidence) != 7
                or evidence[:5] != (_CORE_PUBLIC_COVER_WITNESS, observation.key,
                                    observation.raw_json, observation.parser_id,
                                    observation.parser_version)):
            return None
        document = notice_cover_document_ref(observation.key)
        with service._source_context_lock:
            def live():
                return (scope.current(service) and service.current_identity is scope.identity
                        and service._provider_enabled_for_identity(document.provider_id, scope.identity_id) is True)
            if not live():
                return None
            session = service._sessions.get(service._session_key(document.provider_id, "__public__"))
            if not _anonymous_session(session, document.provider_id):
                return None
            source = read_notice_cover(service.storage, document)
            if (source is None or source.observation.key != observation.key
                    or source.observation.parser_id != observation.parser_id):
                return None
            pin = read_approved_parse_witness(service.storage, key=observation.key,
                                              parser_id=observation.parser_id)
            alert = read_parse_witness_diagnostic(service.storage, key=observation.key,
                                                  parser_id=observation.parser_id)
            if (pin is None or alert is None or alert["generation"] != pin.generation
                    or read_notice_cover(service.storage, document) != source
                    or read_approved_parse_witness(service.storage, key=observation.key,
                                                  parser_id=observation.parser_id) != pin
                    or not live()):
                return None
            names = []
            for field in alert["fields"]:
                index = int(field.removeprefix("critical_field_")) - 1
                if not 0 <= index < len(pin.baseline.critical_fields):
                    return None
                path = pin.baseline.critical_fields[index].path
                if len(path) != 3 or path[:2] != ("search_result", "public_bibliography"):
                    return None
                if path[-1] not in ("title", "record_id", "author", "format",
                                    "publisher", "docbase", "medium"):
                    return None
                names.append(path[-1])
            return {
                "provider": document.provider_id,
                "status": alert["status"],
                "fields": tuple(names),
                "capture_state": alert["capture_state"],
                "last_healthy_at": source.observation.observed_at,
                "generation": pin.generation,
            }
    except (ValueError, TypeError, AttributeError, KeyError, RuntimeError, OSError):
        return None


def private_search_witness(service, row, *, scope, approved=None):
    """Propose or explicitly approve a private same-call digest under the existing CAS.

    No retained body is required: the admitted producer hashed the actual HTTP
    response before decoding. The Core receipt is bound to the live account,
    original row, source revision and installed parser. No GET or raw export.
    """
    from .notice_c_private_search_web import (
        _MARKER, _PARSE_MARKER, _WITNESS, _prepare, _sql_binding_current,
    )
    from .notice_cover_repository import _checked_source_state, _opaque_key
    from .notice_parse_witness_store import _prepare_approved_parse_witness_digest, _decode, _PREFIX
    from .notice_parse_witness import admitted_source_scope_sha256
    from sqlite3 import Error as SQLiteError

    if (type(row) is not SearchResult or type(scope) is not SearchReadScope
            or (approved is not None and type(approved) is not PublicWitnessProposal)):
        return None
    try:
        with service._source_context_lock:
            if not scope.current(service) or service.current_identity is not scope.identity:
                return None
            prepared = _prepare(service, row, scope.identity_id)
            if prepared is None:
                return None
            call, observation, document = prepared
            packet = row.__dict__[_MARKER].parse_input
            stamp = row.__dict__.get(_PARSE_MARKER)
            if (packet is None or type(stamp) is not tuple or len(stamp) != 5
                    or stamp[0] is not _WITNESS or type(stamp[1]) is not int
                    or stamp[2:] != (observation.source_revision, packet[0], packet[1])):
                return None
            bibliography = observation.fields_copy()["private_bibliography"]
            fields = tuple(CriticalParseField(
                ("private_bibliography", name), "string",
                expected_sha256=field_digest(bibliography[name]),
            ) for name in packet[2])
            storage = service.storage
            with storage._lock, storage._connect() as db:
                db.execute("BEGIN IMMEDIATE" if approved is not None else "BEGIN")
                if not _sql_binding_current(db, document, call.binding.connection_epoch):
                    return None
                _, source, _ = _checked_source_state(db, document, _opaque_key(document))
                if (source is None or source.epoch != stamp[1]
                        or source.observation.source_revision != observation.source_revision
                        or source.observation.key != observation.key
                        or source.observation.raw_json != observation.raw_json):
                    return None
                key = admitted_source_scope_sha256(observation.key, parser_id=observation.parser_id)
                saved = db.execute("SELECT value FROM metadata WHERE key=?",
                                   (_PREFIX + key,)).fetchone()
                generation = _decode(saved[0], key).generation if saved is not None else 0
                proposal = PublicWitnessProposal(
                    document, source.epoch, (packet[0], packet[1], observation.source_revision,
                                            call.binding.connection_epoch, call.binding.session_epoch),
                    generation, fields,
                )
                if approved is None:
                    return proposal
                if approved != proposal:
                    return None
                key, baseline = _prepare_approved_parse_witness_digest(
                    observation=observation, input_digest=packet[0], critical_fields=fields,
                    expected_generation=generation, parser_implementation_sha256=packet[1],
                )
                return _publish_approved_parse_witness_txn(
                    db, key, baseline, expected_generation=generation,
                )
    except (ValueError, TypeError, AttributeError, KeyError, RuntimeError, OSError, SQLiteError):
        return None


def private_search_witness_diagnostic(service, row, *, scope):
    """Read the selected exact private witness, never from a foreign or revoked session."""
    from .notice_c_private_search_web import _prepare
    from .notice_parse_witness_alert import read_parse_witness_diagnostic
    from sqlite3 import Error as SQLiteError

    if type(row) is not SearchResult or type(scope) is not SearchReadScope:
        return None
    try:
        with service._source_context_lock:
            if not scope.current(service) or service.current_identity is not scope.identity:
                return None
            prepared = _prepare(service, row, scope.identity_id)
            if prepared is None:
                return None
            call, observation, document = prepared
            source = read_notice_cover(service.storage, document)
            if source is None:
                return None
            pin = read_approved_parse_witness(service.storage, key=observation.key,
                                              parser_id=observation.parser_id)
            alert = read_parse_witness_diagnostic(service.storage, key=observation.key,
                                                  parser_id=observation.parser_id)
            if (pin is None or alert is None or pin.generation != alert["generation"]
                    or read_notice_cover(service.storage, document) != source
                    or read_approved_parse_witness(service.storage, key=observation.key,
                                                  parser_id=observation.parser_id) != pin
                    or not call.binding.current(service, call.scope)):
                return None
            names = []
            for field in alert["fields"]:
                index = int(field.removeprefix("critical_field_")) - 1
                if not 0 <= index < len(pin.baseline.critical_fields):
                    return None
                path = pin.baseline.critical_fields[index].path
                if (len(path) != 2 or path[0] != "private_bibliography"
                        or path[1] not in ("title", "author", "publisher")):
                    return None
                names.append(path[1])
            return {
                "provider": document.provider_id, "status": alert["status"],
                "fields": tuple(names), "capture_state": alert["capture_state"],
                "last_healthy_at": source.observation.observed_at, "generation": pin.generation,
            }
    except (ValueError, TypeError, AttributeError, KeyError, RuntimeError, OSError, SQLiteError):
        return None
