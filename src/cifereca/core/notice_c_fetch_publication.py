"""#4982 publication of a fresh, exact, explicitly admitted public notice.

A producer must already have reserved its established transport under the
one-use authority and invoked its own qualified parser. This port does not
call a provider, discover an endpoint or promote a detail/Search DTO. It
uses the same source writer and dirty-C requests as the reparse worker.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from threading import get_ident

from . import notice_c_job_journal as journal
from .notice_c_fetch_authority import PublicFetchAuthority, PublicFetchPermit
from .notice_c_work_bridge import PreparedSource, UnavailableNoticeWork, WitnessRejectedPublication
from .notice_parse_witness_alert import review_publication_txn
from .notice_parse_witness_store import _PREFIX as witness_prefix
from .notice_parse_witness import admitted_source_scope_sha256
from .notice_regression_capture_intent import export_capture
from .notice_source_observation import NoticeSourceObservation
from .notice_cover_projection import project_notice_cover_attribute
from .notice_cover_repository import _opaque_key, _checked_source_state, _persist_notice_cover_txn
from .notice_c_public_search import _public_observation
from .notice_c_cover_index import _exact_public_search_evidence
from .notice_c_work_requests import request_source_worker_dependents_txn
from .notice_c_initial_publication import request_initial_publication_txn


@dataclass(frozen=True, slots=True, repr=False)
class FreshPublicParse:
    """Trusted same-call evidence, not a plugin declaration or authorization."""
    observation: NoticeSourceObservation
    body: bytes
    export_allowed: bool = False


def prepare_public_fetch_publication(storage, authority, permit, result, *, clock, capture_directory=None):
    """Prepare publication from the admitted producer's actual observation.

    Trusted adapter only. The result MUST originate in the just-reserved
    authorized transport, not an old cached SearchResult or a guessed
    supplier URL. Transport/parser version authority is the producer's
    responsibility; the receipt checks equality, not network provenance.

    The returned PreparedSource is single-use, and can be run only while
    holding the authority's publication_guard across the final commit.
    The existing bridge/caller must finish the journal lease atomically.
    All refusals after transport admission are permanent for that grant.
    """
    if (type(authority) is not PublicFetchAuthority
            or authority._storage is not storage
            or type(permit) is not PublicFetchPermit
            or not callable(clock)):
        raise ValueError("notice_fetch_invalid_publication_port")

    evidence = result if type(result) is FreshPublicParse else None
    if evidence is not None:
        if (type(evidence.body) is not bytes or not 0 < len(evidence.body) <= 2 * 1024 * 1024
                or type(evidence.export_allowed) is not bool):
            authority.revoke(permit.lease.ref)
            raise UnavailableNoticeWork("notice_fetch_invalid_parse_evidence")
        result = evidence.observation
    old = permit.source
    now = clock()
    if (type(result) is not NoticeSourceObservation
            or result.key != old.observation.key
            or result.parser_id != old.observation.parser_id
            or result.parser_id != "core.search.public-cover"
            or result.parser_version != old.observation.parser_version
            or result.parser_version != "2"
            or old.attribute.field_path != ("search_result", "thumbnail_url")
            or not journal._integer(now, minimum=1)
            or not journal._integer(result.observed_at, minimum=1)
            or not permit.admitted_at <= result.observed_at <= now):
        authority.revoke(permit.lease.ref)
        raise UnavailableNoticeWork("notice_fetch_invalid_observation")

    try:
        attribute = project_notice_cover_attribute(
            result, field_path=old.attribute.field_path, cover_inventory_complete=True,
        )
        if attribute.document != old.document:
            raise UnavailableNoticeWork("notice_fetch_invalid_observation")
    except Exception:
        authority.revoke(permit.lease.ref)
        raise

    verifier = None
    owner = None
    entered = False

    @contextmanager
    def guard():
        nonlocal verifier, owner, entered
        if entered:
            raise UnavailableNoticeWork("notice_fetch_publication_consumed")
        entered = True
        try:
            with authority.publication_guard(permit) as check:
                verifier = check
                owner = get_ident()
                yield
        finally:
            verifier = None
            owner = None

    def publish(db):
        if verifier is None or owner != get_ident() or not db.in_transaction:
            raise UnavailableNoticeWork("notice_fetch_publication_guard_required")
        # The verifier is itself single-use and rechecks the exact journal
        # lease, source vector, consent and deadline before any write.
        current = verifier(db)
        if current != old:
            raise UnavailableNoticeWork("notice_fetch_source_changed")
        now = clock()
        if not journal._integer(now, minimum=1) or result.observed_at > now:
            raise UnavailableNoticeWork("notice_fetch_invalid_clock")
        if evidence is None:
            scope = admitted_source_scope_sha256(result.key, parser_id=result.parser_id)
            if db.execute("SELECT 1 FROM metadata WHERE key=?",
                          (witness_prefix + scope,)).fetchone() is not None:
                # A legacy DTO cannot silently bypass an approved witness.
                raise UnavailableNoticeWork("notice_fetch_parse_evidence_required")
        if evidence is not None:
            reject, diagnostic = review_publication_txn(
                db, current=current.observation, candidate=result,
                input_digest=sha256(evidence.body).hexdigest(),
                implementation_digest=permit.implementation_digest,
            )
            if reject:
                def after_commit():
                    return export_capture(storage, diagnostic, members=(
                        {"response.raw": evidence.body} if evidence.export_allowed else {}
                    ), directory=capture_directory)
                return WitnessRejectedPublication(None if diagnostic is None else after_commit, diagnostic)
        if (result.status != "complete" or attribute.state != "available"
                or not attribute.snapshot_complete):
            raise UnavailableNoticeWork("notice_fetch_invalid_observation")
        key = _opaque_key(old.document)
        published = _persist_notice_cover_txn(
            db, result, attribute, key, old.attribute.field_path, True, current.epoch,
        )
        # Same documentary evidence can be retained by the writer with
        # the original observed_at. The durable watermark must nevertheless
        # attest the new response, without inventing an epoch or N2 event.
        if (published.document != old.document
                or published.observation.source_revision != result.source_revision
                or published.observation.raw_json != result.raw_json
                or published.attribute != attribute):
            raise UnavailableNoticeWork("notice_fetch_invalid_publication")
        row, live, fence = _checked_source_state(db, old.document, key)
        hit = _public_observation(row[0], key_string=key, now=now)
        if (live != published or hit is None or hit.document != old.document
                or hit.last_seen_at < result.observed_at
                or fence is None or fence["last_seen_at"] < result.observed_at):
            raise UnavailableNoticeWork("notice_fetch_invalid_publication")
        _exact_public_search_evidence(published)
        dirty = request_source_worker_dependents_txn(db, published, now=now)
        if dirty.overflow or dirty.refused:
            # Do not publish a half-reconciled source. Unlike CPU reparse,
            # another attempt cannot use the already consumed GET permit.
            raise UnavailableNoticeWork("notice_fetch_dependents_refused")
        # Dirty recompute only discovers existing canonical documents.
        # A source may have been committed with a still-pending first C
        # intention; its old vector must not survive this source revision.
        # The exact initial writer does nothing if self-C already exists.
        request_initial_publication_txn(db, published, now=now)
        # The guard is still held. Recheck the deadline after the dirty-C
        # scan as well; never commit work already expired during calculation.
        if authority._lookup(permit.lease.ref).permit is not permit:
            raise UnavailableNoticeWork("notice_fetch_authority_unavailable")
        return True

    return PreparedSource(publish, guard)
