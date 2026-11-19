"""Explicit Core intent renewal after an exhausted purchase retry budget.

No new observation, transport, timer or automatic retry on ordinary Sync.
The caller supplies the exact current same-read candidate and the expected
terminal generation under an independently pinned authorized source context.
The returned ref is inert until separately admitted by the existing worker.
"""
from __future__ import annotations

from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from ..plugin_api.provider_purchase_enrichment import PurchaseEnrichmentCandidate
from . import notice_c_job_journal as journal
from .notice_c_purchase_intent import _allowed_txn, _spec_txn
from .notice_c_purchase_association import (
    _current_a, _receipt, _matches_refresh, _exact_receipt_source_txn, _store_receipt,
)


def request_explicit_purchase_retry(storage, expected_ref, snapshot, candidate, *,
                                    now, context_current):
    """Renew one exhausted generation; never retry a permanent refusal.

    Preserve prior descriptive C only if this is the proven successor of a
    TTL refresh, with the same semantic candidate and exact source. A changed
    candidate or corrupted receipt cannot re-use the old association.
    """
    journal._check_ref(expected_ref)
    if (type(snapshot) is not ProviderRightsSnapshot
            or snapshot.status not in ("success", "partial") or snapshot.resume_cursor
            or type(candidate) is not PurchaseEnrichmentCandidate
            or not callable(context_current) or not journal._integer(now, minimum=1)):
        raise ValueError("notice_purchase_invalid_retry")
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if context_current() is not True or not _allowed_txn(db, snapshot.scope):
            raise ValueError("notice_purchase_retry_context")
        old = journal._get(db, expected_ref)
        if _spec_txn(db, snapshot, candidate) != old.spec:
            raise ValueError("notice_purchase_retry_changed")
        ref, proof = _current_a(db, candidate.source)
        if proof is None:
            raise ValueError("notice_purchase_retry_changed")
        prior = _receipt(db, ref)
        retain = prior is not None and _matches_refresh(prior, old, ref)
        if retain:
            if prior["observed_at"] > now:
                raise ValueError("notice_purchase_retry_changed")
            retain = _exact_receipt_source_txn(db, prior, candidate)
        successor = journal._retry_exhausted_purchase_txn(db, old, now=now)
        if retain:
            prior["a"] = proof
            prior["refresh_generation"] = successor.generation
            _store_receipt(db, ref, prior)
        if context_current() is not True:
            raise ValueError("notice_purchase_retry_context")
        return successor
