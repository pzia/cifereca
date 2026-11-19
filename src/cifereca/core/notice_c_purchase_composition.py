"""Trusted Core composition of the same-read A-to-public-C enrichment path.

No supplier imports/discovery, new broker, timer, or second inventory read.
The runtime supplies one qualified anonymous producer and a live private
source context guard. This is NOT installed by default.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from . import notice_c_job_journal as journal
from .notice_c_purchase_execution import PurchaseIntentExecution, PreparedPurchaseSource
from .notice_c_purchase_association import (
    record_purchase_association_txn, reattest_current_purchase_association,
)
from .notice_c_purchase_intent import request_purchase_enrichment_intents
from .notice_c_purchase_publication import (
    prepare_purchase_public_observation, publish_purchase_public_txn,
)
from .notice_c_public_search_replay import _anonymous_session
from .notice_c_work_bridge import UnavailableNoticeWork
from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from ..plugin_api.provider_purchase_enrichment import PurchaseEnrichmentCandidate


@dataclass(frozen=True, slots=True)
class PurchaseHandoffResult:
    requested: int
    admitted: int
    deferred: int


def make_purchase_enrichment_sink(execution, *, private_provider_id, public_session,
                                  read_response, hold_context, wake, ttl_seconds=120):
    """Return the existing Sync sink(snapshot, candidates).

    read_response(url, comparison_title, *, check_authority, control) is
    an explicitly qualified producer adapter: one anonymous detail response,
    actual observation timestamp and a session publication guard. It must
    not echo the comparison title into a public row or issue a title search.
    hold_context pins the original private runtime generation, not the
    current viewer; it must provide the same guarantees as execution.attest.

    Bounded handoff (one atomic batch, <=32). Larger input must be chunked
    by the coordinator and explicitly surfaced as deferred, never truncated.
    No worker wait, transport or provider callbacks occur in this sink.
    """
    if (type(execution) is not PurchaseIntentExecution
            or type(private_provider_id) is not str or not private_provider_id
            or not _anonymous_session(public_session, getattr(public_session, "provider_id", None))
            or not all(callable(fn) for fn in (read_response, hold_context, wake))
            or type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300):
        raise ValueError("notice_purchase_invalid_composition")

    def sink(snapshot, candidates):
        if (type(snapshot) is not ProviderRightsSnapshot
                or snapshot.scope.provider_id != private_provider_id
                or type(candidates) is not tuple or not 1 <= len(candidates) <= 32
                or any(type(item) is not PurchaseEnrichmentCandidate for item in candidates)):
            raise ValueError("notice_purchase_invalid_handoff")
        snapshot, candidates = deepcopy(snapshot), deepcopy(candidates)
        now = execution._now()
        with hold_context() as allowed:
            if allowed is not True:
                raise ValueError("notice_purchase_context_changed")
            refs = request_purchase_enrichment_intents(
                execution._storage, snapshot, candidates, now=now,
                context_current=lambda: allowed is True,
            )
        admitted = deferred = 0
        for ref, candidate in zip(refs, candidates):
            job = journal.read_job(execution._storage, ref)
            if job.state == "current":
                with hold_context() as allowed:
                    if allowed is not True:
                        deferred += 1
                        continue
                    ref = reattest_current_purchase_association(
                        execution._storage, ref, snapshot, candidate, now=execution._now(),
                    )
                job = journal.read_job(execution._storage, ref)
                if job.state == "current":
                    continue
            spec = job.spec

            def execute(selected, check_authority, control, *, spec=spec):
                if not _anonymous_session(public_session, getattr(public_session, "provider_id", None)):
                    raise UnavailableNoticeWork("notice_purchase_public_session_unavailable")
                control.check_cancelled()
                response, observed_at, guard = read_response(
                    selected.public_locator, selected.title,
                    check_authority=check_authority, control=control,
                )
                if not callable(guard) or not check_authority():
                    raise UnavailableNoticeWork("notice_purchase_public_response_unavailable")
                observation = prepare_purchase_public_observation(
                    response, public_session, selected, observed_at=observed_at,
                )
                if observed_at > execution._now():
                    raise ValueError("notice_purchase_invalid_observation_time")

                def publish(db, lease):
                    now = execution._now()
                    publication = publish_purchase_public_txn(
                        db, spec, snapshot, selected, observation, now=now,
                    )
                    record_purchase_association_txn(
                        db, lease, snapshot, selected, publication, observed_at=observed_at, now=now,
                    )
                    return True

                return PreparedPurchaseSource(publish, guard)

            try:
                if execution.attest(
                    ref, snapshot, candidate, hold_context=hold_context,
                    execute=execute, ttl_seconds=ttl_seconds,
                ):
                    admitted += 1
                else:
                    deferred += 1
            except ValueError:
                # Admission pressure or revoked context does not roll back A
                # or the durable intentions already committed above.
                deferred += 1
        if admitted:
            wake()
        return PurchaseHandoffResult(len(refs), admitted, deferred)

    return sink
