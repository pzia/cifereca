"""#4988: bind an inert lender roster to an already admitted canonical C.

No title/ISBN join, provider request, fabricated native row or write.
The Core caller supplies original occurrences, never a public projection.
The selected occurrence remains the ordinary executor's exact notice.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import SearchResult
from .borrow_for_identity_discovery import (
    PossibleBorrowLender, discover_possible_borrow_lenders,
    possible_lender_matches, possible_lender_session_matches,
)
from .borrow_action_scope import borrow_claim_key
from .borrow_for_identity_native_occurrence import native_borrow_occurrence_document
from .notice_c_cover_index import (
    _db_key, _decode, is_canonical_cover_document,
)
from .notice_cover_repository import _checked_source_state, _opaque_key

_TOKEN = object()


@dataclass(frozen=True, slots=True, repr=False)
class NativeLenderRoster:
    document: object
    epoch: int
    rows: tuple[PossibleBorrowLender, ...]
    _service: object = field(repr=False)
    _reader: object = field(repr=False)
    _reader_id: str
    _account_epoch: int
    _source_epoch: int
    _sources: tuple = field(repr=False)
    _token: object = field(repr=False)


def _reader_current(service, reader, reader_id, account_epoch, source_epoch):
    return (type(reader_id) is str and bool(reader_id)
            and type(account_epoch) is int and account_epoch >= 0
            and type(source_epoch) is int and source_epoch >= 0
            and service.identity_by_id(reader_id) is reader
            and getattr(reader, "identity_id", None) == reader_id
            and service._account_context_epoch == account_epoch
            and service._source_context_epoch == source_epoch)


def _membership(service, document, sources):
    """One canonical/source SQLite snapshot, never synthesize a source."""
    if not is_canonical_cover_document(document):
        return None
    storage = service.storage
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        raw = db.execute("SELECT value FROM metadata WHERE key=?",
                         (_db_key(document),)).fetchone()
        if raw is None:
            return None
        c = _decode(raw[0], document)
        members = {m.source_ref: m for m in c.sources}
        result = []
        for source in sources:
            member = members.get(source.source_key)
            if (member is None or member.visibility != "private"
                    or member.provider_id != source.provider_id):
                return None
            _, live, fence = _checked_source_state(db, source, _opaque_key(source))
            if (live is None or fence is None
                    or live.epoch != member.source_epoch
                    or live.observation.source_revision != member.source_revision
                    or live.observation.status != "complete"):
                return None
            result.append((source.source_key, member.source_epoch,
                           member.source_revision, member.relation_proof_id))
        return c.epoch, tuple(result)


def capture_native_lender_roster(service, *, document, expected_epoch,
                                 requesting_identity_id, occurrences):
    """Worker-only discovery for an explicitly admitted C and native rows."""
    if (not is_canonical_cover_document(document)
            or type(expected_epoch) is not int or not 1 <= expected_epoch < 2**63
            or type(requesting_identity_id) is not str or not requesting_identity_id
            or type(occurrences) is not tuple or not 1 <= len(occurrences) <= 64
            or any(type(row) is not SearchResult for row in occurrences)
            or len({id(row) for row in occurrences}) != len(occurrences)):
        return None
    try:
        reader = service.identity_by_id(requesting_identity_id)
        ae, se = service._account_context_epoch, service._source_context_epoch
        if not _reader_current(service, reader, requesting_identity_id, ae, se):
            return None
        rows = discover_possible_borrow_lenders(
            service, requesting_identity_id=requesting_identity_id, notices=occurrences,
        )
        if not rows:
            return None
        sources = []
        for row in rows:
            source = native_borrow_occurrence_document(
                service, row._notice, owner=row.lender_identity_id,
            )
            if source is None:
                return None
            sources.append(source)
        if len(set(sources)) != len(sources):
            return None
        proof = _membership(service, document, tuple(sources))
        if proof is None or proof[0] != expected_epoch:
            return None
        roster = NativeLenderRoster(document, expected_epoch, rows, service,
                                    reader, requesting_identity_id, ae, se,
                                    proof[1], _TOKEN)
        return roster if native_lender_roster_current(service, roster) else None
    except Exception:
        return None


def native_lender_roster_current(service, roster):
    """Worker-only full original-roster revalidation, no provider I/O.

    Do not rediscover after a loan claim has been acquired: the selected
    durable claim is intentionally spent by then. Membership and exact
    session/occurrence validation remain required independently.
    """
    if (type(roster) is not NativeLenderRoster or roster._token is not _TOKEN
            or roster._service is not service
            or not is_canonical_cover_document(roster.document)
            or type(roster.epoch) is not int or not 1 <= roster.epoch < 2**63
            or type(roster.rows) is not tuple or not 1 <= len(roster.rows) <= 64
            or any(type(row) is not PossibleBorrowLender for row in roster.rows)
            or type(roster._sources) is not tuple
            or len(roster._sources) != len(roster.rows)
            or any(type(p) is not tuple or len(p) != 4
                   or type(p[0]) is not str or type(p[1]) is not int
                   or type(p[2]) is not str or type(p[3]) is not str
                   for p in roster._sources)):
        return False
    try:
        if not _reader_current(service, roster._reader, roster._reader_id,
                               roster._account_epoch, roster._source_epoch):
            return False
        sources, claims = [], set()
        for row in roster.rows:
            notice, account = row._notice, row._account
            if not possible_lender_matches(
                    row, notice, requesting_identity_id=roster._reader_id):
                return False
            if not possible_lender_session_matches(
                    row, service._sessions.get(service._session_key(
                        account.provider_id, account.credential_id))):
                return False
            claim = borrow_claim_key(account, notice)
            if claim is None or claim in claims:
                return False
            claims.add(claim)
            source = native_borrow_occurrence_document(
                service, notice, owner=row.lender_identity_id,
            )
            if source is None:
                return False
            sources.append(source)
        if len(set(sources)) != len(sources):
            return False
        return (_membership(service, roster.document, tuple(sources))
                == (roster.epoch, roster._sources)
                and _reader_current(service, roster._reader, roster._reader_id,
                                    roster._account_epoch, roster._source_epoch))
    except Exception:
        return False


__all__ = ["NativeLenderRoster", "capture_native_lender_roster",
           "native_lender_roster_current"]
