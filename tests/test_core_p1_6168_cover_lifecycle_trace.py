"""#6168 N0: prove decisions, validated bytes and actual paint are separate."""
from __future__ import annotations

from cifereca.core.book_cover_cache import (
    CoverDecisionCache, CoverDecisionChange,
)
from cifereca.core.book_cover_trace import CoverLifecycleTrace
from cifereca.core.book_cover_source_projection import project_exact_loan_cover
from tests.test_core_p1_4979_exact_loan_cover import loan, local_row
import pytest


def selected(current=None, *, revision=1):
    current = current or loan()
    return project_exact_loan_cover(
        current, (local_row(current),), revision=revision,
        snapshot_complete=True,
    )


def begin_with_trace(decision, *, max_scopes=10):
    cache = CoverDecisionCache()
    trace = CoverLifecycleTrace(max_scopes=max_scopes)
    subscription = trace.attach(cache, decision.scope)
    token = cache.begin(decision.scope, revision=decision.revision)
    assert cache.publish_resolved(token, decision)
    return cache, trace, token, subscription


def test_source_decision_media_and_two_gtk_surfaces_have_distinct_gates():
    decision = selected()
    cache, trace, token, handle = begin_with_trace(decision)
    try:
        snapshot = trace.snapshot(decision.scope)
        assert snapshot.status == "selected"
        assert snapshot.resolved and snapshot.evidence_present
        assert not snapshot.media_cached
        assert not snapshot.list_painted and not snapshot.detail_painted
        assert not trace.note_painted(token, surface="list")
        assert trace.note_media(
            token, source_cached=True, preview_cached=False,
        )
        assert trace.snapshot(decision.scope).media_cached
        assert trace.note_painted(token, surface="list")
        assert trace.snapshot(decision.scope).list_painted
        assert not trace.snapshot(decision.scope).detail_painted
        assert trace.note_painted(token, surface="detail")
        assert trace.snapshot(decision.scope).detail_painted
        assert cache.peek(decision.scope, revision=1) == decision
    finally:
        handle.cancel()


def test_source_decode_or_cache_failure_cannot_be_misreported_as_paint():
    decision = selected()
    _cache, trace, token, _handle = begin_with_trace(decision)
    assert trace.note_media(token, source_cached=False)
    assert not trace.note_painted(token, surface="detail")
    trace.note_media(token, source_cached=True, preview_cached=True)
    assert trace.note_painted(token, surface="detail")
    # A corrupted source on a later physical probe revokes the claimed
    # rendered image while retaining the N1 decision provenance.
    assert trace.note_media(token, source_cached=False)
    after = trace.snapshot(decision.scope)
    assert after.status == "selected" and after.resolved
    assert not after.media_cached
    assert not after.preview_cached
    assert not after.detail_painted


def test_source_invalidation_clears_every_earlier_paint_for_aba_identity():
    initial = selected()
    cache, trace, token, _handle = begin_with_trace(initial)
    trace.note_media(token, source_cached=True)
    trace.note_painted(token, surface="list")
    trace.note_painted(token, surface="detail")
    cache.invalidate(initial.scope, cause="account_epoch_changed")
    invalidated = trace.snapshot(initial.scope)
    assert invalidated.invalidated and not invalidated.resolved
    assert not invalidated.list_painted and not invalidated.detail_painted
    assert not trace.note_media(token, source_cached=True)
    assert not trace.note_painted(token, surface="list")
    new_decision = selected(revision=2)
    token2 = cache.begin(initial.scope, revision=2)
    assert cache.publish_resolved(token2, new_decision)
    fresh = trace.snapshot(initial.scope)
    assert fresh.status == "selected"
    assert not fresh.invalidated and not fresh.list_painted
    assert token2.generation > token.generation


def test_other_borrower_same_book_title_cannot_inherit_media_or_paint():
    first = selected()
    second = selected(loan(account="different-account"))
    cache, trace, old, _handle = begin_with_trace(first)
    trace.note_media(old, source_cached=True)
    trace.note_painted(old, surface="list")
    new_scope = second.scope
    handle2 = trace.attach(cache, new_scope)
    token2 = cache.begin(new_scope, revision=1)
    assert cache.publish_resolved(token2, second)
    assert trace.snapshot(first.scope).list_painted
    assert not trace.snapshot(new_scope).media_cached
    assert not trace.snapshot(new_scope).list_painted
    assert not trace.note_painted(token2, surface="list")
    handle2.cancel()


def test_missing_and_partial_evidence_never_claim_loaded_media():
    current = loan()
    pending = project_exact_loan_cover(
        current, (), revision=1, snapshot_complete=False,
    )
    missing = project_exact_loan_cover(
        current, (), revision=2, snapshot_complete=True,
    )
    cache, trace, token, _handle = begin_with_trace(pending)
    assert trace.snapshot(pending.scope).status == "pending"
    assert not trace.snapshot(pending.scope).evidence_present
    assert not trace.note_media(token, source_cached=True)
    token2 = cache.begin(missing.scope, revision=2)
    assert cache.publish_resolved(token2, missing)
    assert trace.snapshot(missing.scope).status == "missing"
    assert not trace.note_media(token2, source_cached=True)


def test_out_of_order_core_event_cannot_rewind_invalidation():
    current = selected()
    trace = CoverLifecycleTrace()
    scope = current.scope
    later = CoverDecisionChange(
        scope=scope, revision=4, generation=44,
        sequence=80, kind="invalidated", cause="evidence_changed",
    )
    earlier = CoverDecisionChange(
        scope=scope, revision=4, generation=43,
        sequence=79, kind="resolved", decision=selected(revision=4),
    )
    trace.consume(later)
    trace.consume(earlier)
    current_state = trace.snapshot(scope)
    assert current_state.invalidated
    assert not current_state.resolved
    assert current_state.generation == 44


def test_bounded_trace_does_not_lend_an_evicted_source_to_another():
    cache = CoverDecisionCache()
    trace = CoverLifecycleTrace(max_scopes=1)
    first = selected()
    second = selected(loan(identity="bob"))
    first_handle = trace.attach(cache, first.scope)
    second_handle = trace.attach(cache, second.scope)
    for item in (first, second):
        token = cache.begin(item.scope, revision=item.revision)
        assert cache.publish_resolved(token, item)
    assert trace.snapshot(first.scope) is None
    assert trace.snapshot(second.scope).status == "selected"
    assert trace.stats() == {"tracked_sources": 1}
    first_handle.cancel()
    second_handle.cancel()


@pytest.mark.parametrize("bad", [0, -1, True, 20001, "100"])
def test_capacity_is_explicit_and_bounded(bad):
    with pytest.raises(ValueError):
        CoverLifecycleTrace(max_scopes=bad)


def test_media_and_paint_reject_malformed_or_unattested_transitions():
    decision = selected()
    cache, trace, token, _handle = begin_with_trace(decision)
    with pytest.raises(TypeError):
        trace.note_media(token, source_cached=1)
    with pytest.raises(ValueError):
        trace.note_media(token, source_cached=False, preview_cached=True)
    with pytest.raises(ValueError):
        trace.note_painted(token, surface="unknown")
    assert not trace.snapshot(decision.scope).media_cached


def test_public_trace_never_exports_identity_book_url_account_or_credential():
    current = loan(
        identity="PRIVATE-OWNER", credential="SECRET-CARD",
        account="SECRET-ACCOUNT",
    )
    decision = selected(current)
    _cache, trace, token, _handle = begin_with_trace(decision)
    trace.note_media(token, source_cached=True, preview_cached=True)
    trace.note_painted(token, surface="detail")
    public = trace.snapshot(decision.scope).public_fields()
    assert set(public) == {
        "revision", "generation", "status", "evidence_present",
        "resolved", "media_cached", "preview_cached",
        "list_painted", "detail_painted", "invalidated", "sequence",
    }
    for raw in (
        "PRIVATE-OWNER", "SECRET-CARD", "SECRET-ACCOUNT",
        decision.uri, decision.scope.source_key,
    ):
        assert raw not in repr(public)
    assert public["status"] == "selected"
    assert public["detail_painted"] is True
