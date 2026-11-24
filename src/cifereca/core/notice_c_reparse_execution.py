"""#4982 trusted Core registration and transaction port for cache-only reparse.

No provider imports, plugin discovery, network fallback, extra queue or pool.
Registration is explicit in the trusted composition root, not a claim made
by a returned parser object. No registration is installed by default.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Callable

from . import notice_c_job_journal as journal
from . import notice_c_raw_input_receipt as raw
from .notice_c_reparse_requests import (
    read_public_reparse_request, check_public_reparse_inputs_txn,
)
from .notice_cover_repository import _opaque_key, _persist_notice_cover_txn
from .notice_cover_projection import project_notice_cover_attribute
from .notice_source_observation import NoticeSourceObservation, _id
from .notice_parse_witness_alert import review_publication_txn
from .notice_regression_capture_intent import export_capture, read_replay_capture_locator
from .notice_c_work_bridge import WitnessRejectedPublication
from .notice_c_public_search import _public_observation
from .notice_c_cover_index import _exact_public_search_evidence
from .notice_c_work_requests import request_source_worker_dependents_txn
from .notice_c_initial_publication import request_initial_publication_txn
from .notice_c_work_bridge import PreparedSource, RetryableNoticeWork, UnavailableNoticeWork


@dataclass(frozen=True, slots=True, repr=False)
class ReplayInput:
    producer_token: str
    transport_digest: str
    parser_family_digest: str
    original_implementation_digest: str
    body: bytes


@dataclass(frozen=True, slots=True, repr=False)
class PublicReplayPort:
    provider_id: str
    parser_family_digest: str
    implementation_digest: str
    # All ports are trusted Core adapters; nothing here is a plugin API.
    read_input: Callable
    parse: Callable
    # Return context manager yielding exact ReplayInput or None; must
    # acquire only the provider's cache/session guard, NEVER Storage.
    # Exit must release the guard without raising or suppressing exceptions.
    hold_input: Callable
    # Trusted Core policy, never supplied by the parser. Default denies export.
    authorize_capture_input: Callable | None = None


class PublicReplayRegistry:
    """Immutable, bounded table supplied explicitly by trusted composition."""

    def __init__(self, ports=()):
        if type(ports) is not tuple or len(ports) > 8:
            raise ValueError("notice_reparse_invalid_registry")
        keys = set()
        for port in ports:
            if (type(port) is not PublicReplayPort or not _id(port.provider_id)
                    or not journal._digest(port.parser_family_digest)
                    or not journal._digest(port.implementation_digest)
                    or not all(callable(fn) for fn in (
                        port.read_input, port.parse, port.hold_input,
                    )) or (port.authorize_capture_input is not None
                           and not callable(port.authorize_capture_input))):
                raise ValueError("notice_reparse_invalid_registry")
            key = (port.provider_id, port.parser_family_digest, port.implementation_digest)
            if key in keys:
                raise ValueError("notice_reparse_ambiguous_registry")
            keys.add(key)
        self._ports = ports

    def supported(self):
        return tuple(sorted({("reparse_cached_input", p.implementation_digest)
                             for p in self._ports}))

    def select(self, request):
        matches = [p for p in self._ports
                   if p.provider_id == request.source.observation.key.provider_id
                   and p.parser_family_digest == request.receipt.parser_family_digest
                   and p.implementation_digest == request.implementation_digest]
        if len(matches) != 1:
            raise UnavailableNoticeWork("notice_reparse_parser_unavailable")
        return matches[0]


def _match_input(request, selected):
    receipt = request.receipt
    if (type(selected) is not ReplayInput
            or selected.producer_token != receipt.producer_token
            or selected.transport_digest != receipt.transport_digest
            or selected.parser_family_digest != receipt.parser_family_digest
            or selected.original_implementation_digest != receipt.parser_implementation_digest
            or type(selected.body) is not bytes
            or len(selected.body) != receipt.byte_count
            or raw._body_digest(selected.body) != receipt.input_digest):
        raise UnavailableNoticeWork("notice_reparse_input_unavailable")


def prepare_public_reparse(storage, registry, spec, control, *, clock):
    """Parse outside SQLite, hold exact producer lease across final commit.

    The selected parser must return a Core-admitted public observation via
    the established admission projector, not arbitrary plugin raw fields.
    Accept exact Search v2 with an available cover or explicit cover
    omission. No downgrade from available, rights, identity change or GET.
    """
    if type(registry) is not PublicReplayRegistry:
        raise ValueError("notice_reparse_invalid_registry")
    try:
        request = read_public_reparse_request(storage, spec, now=clock())
    except ValueError as exc:
        raise UnavailableNoticeWork("notice_reparse_input_unavailable") from exc
    old = request.source
    coverless = (raw._coverless_public_search_observation(old.observation)
                 and old.attribute.state == "pending" and not old.attribute.candidates)
    if (old.observation.parser_id != "core.search.public-cover"
            or old.observation.parser_version != "2"
            or (not coverless and (old.observation.status != "complete"
                                   or old.attribute.state != "available"))
            or old.attribute.field_path != ("search_result", "thumbnail_url")):
        raise UnavailableNoticeWork("notice_reparse_unsupported_source")
    port = registry.select(request)
    token = request.receipt.producer_token
    control.check_cancelled()
    selected = port.read_input(token)
    _match_input(request, selected)
    control.check_cancelled()
    result = port.parse(request, selected)
    control.check_cancelled()
    if (type(result) is not NoticeSourceObservation
            or result.key != old.observation.key
            or result.parser_id != old.observation.parser_id
            or result.parser_version != old.observation.parser_version
            or (result.status != "complete" and not (
                coverless and raw._coverless_public_search_observation(result)))
            # Parsing an existing response NEVER advances source freshness.
            or result.observed_at != old.observation.observed_at):
        raise UnavailableNoticeWork("notice_reparse_invalid_observation")
    attribute = project_notice_cover_attribute(
        result, field_path=old.attribute.field_path, cover_inventory_complete=True,
    )
    if (attribute.document != old.document
            or (attribute.state != "available" and not (
                coverless and raw._coverless_public_search_observation(result)
                and attribute.state == "pending" and not attribute.candidates))):
        raise UnavailableNoticeWork("notice_reparse_invalid_observation")

    @contextmanager
    def guard():
        # Lock order: provider cache/session first, then Storage. Preparation
        # and parsing must not hold either lock. All writes occur in publish.
        with port.hold_input(token) as live:
            _match_input(request, live)
            if live != selected:
                raise UnavailableNoticeWork("notice_reparse_input_changed")
            yield

    def publish(db):
        now = clock()
        current = check_public_reparse_inputs_txn(
            db, spec, request, selected.body,
            producer_token=selected.producer_token,
            transport_digest=selected.transport_digest,
            parser_family_digest=selected.parser_family_digest,
            implementation_digest=port.implementation_digest, now=now,
        )
        reject, diagnostic = review_publication_txn(
            db, current=current.observation, candidate=result,
            input_digest=request.receipt.input_digest,
            implementation_digest=port.implementation_digest, replay_spec=spec,
        )
        if reject:
            def export_after_commit():
                return export_capture(storage, diagnostic, members=
                                      _authorized_capture_members(port, request, selected))
            return WitnessRejectedPublication(
                None if diagnostic is None else export_after_commit,
                diagnostic,
            )
        persisted = _persist_notice_cover_txn(
            db, result, attribute, _opaque_key(current.document),
            old.attribute.field_path, True, current.epoch,
        )
        if persisted.observation != result:
            raise UnavailableNoticeWork("notice_reparse_invalid_publication")
        key = _opaque_key(persisted.document)
        row, = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        hit = _public_observation(row, key_string=key, now=now)
        if hit is None or hit.document != old.document:
            raise UnavailableNoticeWork("notice_reparse_invalid_publication")
        _exact_public_search_evidence(persisted)
        # No source change is visible without its dirty-C requests. A
        # capacity/ambiguity refusal rolls back the entire attempt.
        dirty = request_source_worker_dependents_txn(db, persisted, now=now)
        if dirty.overflow or dirty.refused:
            raise RetryableNoticeWork("notice_reparse_dependents_deferred")
        # Dirty recompute only discovers existing canonical documents.
        # A source may have been committed with a still-pending first C
        # intention; its old vector must not survive this source revision.
        # The exact initial writer does nothing if self-C already exists.
        request_initial_publication_txn(db, persisted, now=now)
        return True

    return PreparedSource(publish, guard)


def _authorized_capture_members(port, request, selected):
    """Fail closed; only an explicit trusted decision permits exact bytes."""
    if port.authorize_capture_input is not None:
        try:
            if port.authorize_capture_input(request, selected) is True:
                return {"response.raw": selected.body}
        except Exception:
            pass
    return {}


def resolve_replay_capture_members(storage, registry, diagnostic, *, clock):
    """Recover exact authorized bytes via existing TTL receipt, never GET.

    Missing, replaced or expired inputs yield an explicitly partial capture.
    The registry is trusted Core composition; it must reauthorize export.
    No producer token, raw body or session is added to durable metadata.
    """
    locator = read_replay_capture_locator(storage, diagnostic)
    if locator is None or registry is None:
        return {}
    spec, payload = locator
    try:
        request = read_public_reparse_request(storage, spec, now=clock())
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected_id = sha256((request.receipt.source_scope_digest + ":" + encoded).encode()).hexdigest()
        if request.receipt.input_digest != payload["input_sha256"] or expected_id != diagnostic:
            return {}
        port = registry.select(request)
        if port.authorize_capture_input is None:
            return {}
        with port.hold_input(request.receipt.producer_token) as live:
            _match_input(request, live)
            # Same lock ordering as normal replay: producer guard then SQLite.
            with storage._lock, storage._connect() as db:
                db.execute("BEGIN")
                check_public_reparse_inputs_txn(
                    db, spec, request, live.body,
                    producer_token=live.producer_token,
                    transport_digest=live.transport_digest,
                    parser_family_digest=live.parser_family_digest,
                    implementation_digest=port.implementation_digest, now=clock(),
                )
        return _authorized_capture_members(port, request, live)
    except (ValueError, UnavailableNoticeWork):
        return {}
