"""Trusted Core composition of the same-read A-to-public-C enrichment path.

No supplier imports/discovery, new broker, timer, or second inventory read.
The runtime supplies one qualified anonymous producer and a live private
source context guard. This is NOT installed by default.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import nullcontext
from dataclasses import dataclass, replace

from . import notice_c_job_journal as journal
from .notice_c_purchase_execution import PurchaseIntentExecution, PreparedPurchaseSource
from .notice_c_purchase_association import (
    record_purchase_association_txn, reattest_current_purchase_association,
)
from .notice_c_purchase_intent import _MAX_BATCH
from .notice_c_enrichment_intent import ExistingPurchaseIntent, request_notice_enrichment
from .notice_c_purchase_publication import (
    prepare_purchase_public_observation, publish_purchase_public_txn,
)
from .notice_c_public_search_replay import _anonymous_session
from .notice_c_purchase_public_detail_cache import read_public_detail_txn, remember_public_detail_txn
from .notice_c_work_bridge import UnavailableNoticeWork
from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from ..plugin_api.provider_purchase_enrichment import PurchaseEnrichmentCandidate


@dataclass(frozen=True, slots=True)
class PurchaseHandoffResult:
    requested: int
    admitted: int
    deferred: int
    unrequested: int = 0


def make_purchase_enrichment_sink(execution, *, private_provider_id, public_session,
                                  read_response, hold_context, wake, ttl_seconds=120,
                                  input_current_txn=None, on_changed=None):
    """Return the existing Sync sink(snapshot, candidates).

    read_response(url, comparison_title, *, check_authority, control) is
    an explicitly qualified producer adapter: one anonymous detail response,
    actual observation timestamp and a session publication guard. It must
    not echo the comparison title into a public row or issue a title search.
    hold_context pins the original private runtime generation, not the
    current viewer; it must provide the same guarantees as execution.attest.

    Bounded atomic chunks of <=32, from one already read inventory. Intent
    persistence is independent of the limited live admission registry.
    An interrupted handoff reports the unrequested suffix explicitly; a
    future qualified inventory can resume durable deferred work.
    No worker wait, transport or provider callbacks occur in this sink.
    """
    if (type(execution) is not PurchaseIntentExecution
            or type(private_provider_id) is not str or not private_provider_id
            or not _anonymous_session(public_session, getattr(public_session, "provider_id", None))
            or not all(callable(fn) for fn in (read_response, hold_context, wake))
            or (input_current_txn is not None and not callable(input_current_txn))
            or (on_changed is not None and not callable(on_changed))
            or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300):
        raise ValueError("notice_purchase_invalid_composition")

    public_provider_id = public_session.provider_id

    def sink(snapshot, candidates):
        if (type(snapshot) is not ProviderRightsSnapshot
                or snapshot.scope.provider_id != private_provider_id
                or snapshot.status not in ("success", "partial") or snapshot.resume_cursor
                or type(candidates) is not tuple or not 1 <= len(candidates) <= 10_000
                or any(type(item) is not PurchaseEnrichmentCandidate for item in candidates)):
            raise ValueError("notice_purchase_invalid_handoff")
        snapshot, candidates = deepcopy(snapshot), deepcopy(candidates)
        # Validate the entire candidate coordinate set before the first
        # write, including ambiguity spanning different chunks. Keep only
        # exact positive rows from this same read, not a new inventory claim.
        rows = {(row.edition_id, row.right_id, row.kind): row for row in snapshot.rights}
        seen = set()
        proofs = []
        for candidate in candidates:
            source = candidate.source
            coordinate = (source.edition_id, source.resource_id, source.kind)
            row = rows.get(coordinate)
            if (source.scope != snapshot.scope or source.branch != snapshot.branch
                    or coordinate in seen or row is None or not row.provider_attested
                    or row.state != "active" or row.observed_at != candidate.observed_at):
                raise ValueError("notice_purchase_invalid_handoff")
            seen.add(coordinate)
            proofs.append(row)

        requested = admitted = deferred = 0
        for offset in range(0, len(candidates), _MAX_BATCH):
            chunk = candidates[offset:offset + _MAX_BATCH]
            # Never present a subset as a complete branch.
            portion = replace(
                snapshot, rights=tuple(proofs[offset:offset + _MAX_BATCH]),
                status="partial", coverage="partial", pagination_complete=None,
            )
            try:
                with hold_context() as allowed:
                    if allowed is not True:
                        raise ValueError("notice_purchase_context_changed")
                    admission = request_notice_enrichment(
                        execution._storage,
                        ExistingPurchaseIntent(portion, chunk, lambda: allowed is True),
                        now=execution._now(),
                    )
                    refs, changed = admission.refs, admission.changed
            except ValueError:
                if not requested:
                    raise
                break
            if changed and on_changed is not None:
                on_changed()
            requested += len(refs)
            accepted, waiting = offer(portion, chunk, refs)
            admitted += accepted
            deferred += waiting
        if admitted:
            wake()
        return PurchaseHandoffResult(requested, admitted, deferred, len(candidates) - requested)

    def offer(snapshot, candidates, refs):
        admitted = deferred = 0
        for ref, candidate in zip(refs, candidates):
            try:
                job = journal.read_job(execution._storage, ref)
                if job.state in ("current", "pending", "error"):
                    with hold_context() as allowed:
                        if allowed is not True:
                            deferred += 1
                            continue
                        ref, changed = reattest_current_purchase_association(
                            execution._storage, ref, snapshot, candidate, now=execution._now(),
                            report_change=True,
                        )
                    if changed and on_changed is not None:
                        on_changed()
                    job = journal.read_job(execution._storage, ref)
                    if job.state == "current":
                        continue
                spec = job.spec
            except ValueError:
                deferred += 1
                continue

            def execute(selected, check_authority, control, *, spec=spec):
                if not _anonymous_session(public_session, public_provider_id):
                    raise UnavailableNoticeWork("notice_purchase_public_session_unavailable")
                control.check_cancelled()
                # Reuse only independently committed public detail proof.
                # The current private candidate still needs its own live admission.
                with execution._storage._lock, execution._storage._connect() as db:
                    db.execute("BEGIN")
                    cached = read_public_detail_txn(
                        db, public_provider_id, selected, now=execution._now(),
                    )
                reused = cached is not None
                if reused:
                    observation = cached.observation
                    observed_at, guard = cached.observed_at, nullcontext
                else:
                    response, observed_at, guard = read_response(
                        selected.public_locator, selected.title,
                        check_authority=check_authority, control=control,
                    )
                    observation = prepare_purchase_public_observation(
                        response, public_session, selected, observed_at=observed_at,
                    )
                if not callable(guard) or check_authority() is not True:
                    raise UnavailableNoticeWork("notice_purchase_public_response_unavailable")
                if observed_at > execution._now():
                    raise ValueError("notice_purchase_invalid_observation_time")

                def publish(db, lease):
                    if input_current_txn is not None and input_current_txn(db) is not True:
                        raise UnavailableNoticeWork("notice_purchase_context_changed")
                    now = execution._now()
                    if reused and read_public_detail_txn(db, public_provider_id, selected, now=now) != cached:
                        raise UnavailableNoticeWork("notice_purchase_public_detail_changed")
                    publication = publish_purchase_public_txn(
                        db, spec, snapshot, selected, observation, now=now,
                    )
                    record_purchase_association_txn(
                        db, lease, snapshot, selected, publication, observed_at=observed_at, now=now,
                    )
                    if not reused:
                        remember_public_detail_txn(db, public_provider_id, selected, publication, observation)
                    return True

                return PreparedPurchaseSource(publish, guard)

            try:
                if execution.attest(
                    ref, snapshot, candidate, hold_context=hold_context,
                    execute=execute, ttl_seconds=ttl_seconds,
                    public_provider_id=public_provider_id,
                ):
                    admitted += 1
                else:
                    deferred += 1
            except ValueError:
                # Admission pressure or revoked context does not roll back A
                # or the durable intentions already committed above.
                deferred += 1
        return admitted, deferred

    return sink
