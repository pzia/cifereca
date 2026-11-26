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
    _db_key, _decode, is_canonical_cover_document, read_consolidated_notice_cover,
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
    _seal: tuple = field(repr=False)


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
                                    proof[1], _TOKEN,
                                    (document.source_key, expected_epoch, tuple(id(row) for row in rows),
                                     id(reader), requesting_identity_id, ae, se, proof[1]))
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
        seal = (roster.document.source_key, roster.epoch, tuple(id(row) for row in roster.rows),
                id(roster._reader), roster._reader_id, roster._account_epoch,
                roster._source_epoch, roster._sources)
        if (type(roster._seal) is not tuple or roster._seal != seal
                or not _reader_current(service, roster._reader, roster._reader_id,
                                       roster._account_epoch, roster._source_epoch)):
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


def _detail_binding(item, identity_id):
    """Recognize only existing native C receipts, never metadata hints."""
    from .notice_c_recommendation_known import known_public_c_binding_facts
    from .notice_c_known_projection import matching_live_public_canonical_ref
    from .notice_c_cached_revision import matching_cached_public_canonical_ref
    from .notice_c_live_revision import persisted_live_c_revision
    known = known_public_c_binding_facts(item)
    if known is not None:
        return known
    for matcher in (matching_live_public_canonical_ref, matching_cached_public_canonical_ref):
        doc = matcher(item, identity_id=identity_id)
        if doc is not None:
            epoch = persisted_live_c_revision(item, doc)
            if epoch is not None:
                return doc, epoch
    return None


def native_lender_roster_for_detail(service, item, *, requesting_identity_id):
    """Read existing C membership and original native rows; no new Search.

    The bool distinguishes a C-bound detail with zero qualified occurrences
    from a legacy exact-source detail. Never fall back to a reconstructed
    public row as an executable loan occurrence.
    """
    if type(item) is not SearchResult:
        return False, None
    try:
        binding = _detail_binding(item, requesting_identity_id)
        if binding is None:
            return False, None
        document, epoch = binding
        return True, native_lender_roster_for_c(
            service, document, expected_epoch=epoch,
            requesting_identity_id=requesting_identity_id,
        )
    except Exception:
        return True, None


def native_lender_roster_for_c(service, document, *, expected_epoch, requesting_identity_id):
    """Worker port for a C-first detail whose original UI receipt is pinned."""
    from .borrow_for_identity_native_occurrence import retained_native_borrow_occurrences
    if (not is_canonical_cover_document(document)
            or type(expected_epoch) is not int or not 1 <= expected_epoch < 2**63):
        return None
    try:
        c = read_consolidated_notice_cover(service.storage, document)
        if c is None or c.epoch != expected_epoch:
            return None
        refs = frozenset(m.source_ref for m in c.sources if m.visibility == "private")
        rows = retained_native_borrow_occurrences(service, source_refs=refs)
        if not rows:
            return None
        return capture_native_lender_roster(
            service, document=document, expected_epoch=expected_epoch,
            requesting_identity_id=requesting_identity_id, occurrences=rows,
        )
    except Exception:
        return None


def native_roster_matches_detail(service, roster, item, *, requesting_identity_id):
    """Pure GTK check of an original SearchResult's own C receipt."""
    try:
        binding = _detail_binding(item, requesting_identity_id)
        return (binding is not None and native_roster_matches_document(
            service, roster, binding[0], expected_epoch=binding[1],
            requesting_identity_id=requesting_identity_id,
        ))
    except Exception:
        return False


def native_roster_matches_document(service, roster, document, *, expected_epoch,
                                   requesting_identity_id):
    """Pure GTK check; original view receipt and SQLite worker remain mandatory."""
    try:
        if (type(roster) is not NativeLenderRoster or roster._token is not _TOKEN
                or roster._service is not service
                or type(roster._reader_id) is not str
                or roster._reader_id != requesting_identity_id
                or not _reader_current(service, roster._reader, roster._reader_id,
                                       roster._account_epoch, roster._source_epoch)
                or not is_canonical_cover_document(roster.document)
                or not is_canonical_cover_document(document)
                or type(expected_epoch) is not int or roster.epoch != expected_epoch
                or roster.document != document
                or type(roster.rows) is not tuple or not 1 <= len(roster.rows) <= 64
                or any(type(row) is not PossibleBorrowLender for row in roster.rows)
                or len({id(row) for row in roster.rows}) != len(roster.rows)):
            return False
        seal = (roster.document.source_key, roster.epoch, tuple(id(row) for row in roster.rows),
                id(roster._reader), roster._reader_id, roster._account_epoch,
                roster._source_epoch, roster._sources)
        return (type(roster._seal) is tuple and roster._seal == seal
                and all(possible_lender_matches(
                    row, row._notice, requesting_identity_id=requesting_identity_id,
                ) and possible_lender_session_matches(
                    row, service._sessions.get(service._session_key(
                        row._account.provider_id, row._account.credential_id)),
                ) for row in roster.rows))
    except Exception:
        return False


__all__ = ["NativeLenderRoster", "capture_native_lender_roster",
           "native_lender_roster_current", "native_lender_roster_for_detail",
           "native_roster_matches_detail", "native_lender_roster_for_c",
           "native_roster_matches_document"]
