"""P1 #6168: post-texture N0/N4 can only lease the live exact N2 decision."""
from __future__ import annotations

from dataclasses import replace
import pytest

from cifereca.core.book_cover_cache import CoverDecisionCache
from cifereca.core.book_cover_trace import CoverLifecycleTrace
from cifereca.core.book_cover_source_projection import project_exact_loan_cover
from tests.test_core_p1_4979_exact_loan_cover import loan, local_row


def selected(current=None, *, revision=1):
    current = current or loan()
    return project_exact_loan_cover(
        current, (local_row(current),),
        revision=revision, snapshot_complete=True,
    )


def publish(cache, decision):
    token = cache.begin(decision.scope, revision=decision.revision)
    assert cache.publish_resolved(token, decision)
    return token


def test_post_paint_lease_matches_actual_selected_scope_revision_and_uri():
    cache = CoverDecisionCache()
    decision = selected()
    source_token = publish(cache, decision)
    lease = cache.selected_token(
        decision.scope, revision=decision.revision, uri=decision.uri,
    )
    assert lease == source_token
    assert lease is not source_token
    assert lease.scope is not source_token.scope
    assert cache.peek(decision.scope, revision=1) is decision
    assert cache.selected_token(
        decision.scope, revision=1, uri="https://other.example/image.jpg",
    ) is None
    assert cache.selected_token(
        decision.scope, revision=2, uri=decision.uri,
    ) is None
    assert cache.selected_token(
        decision.scope, revision=1, uri="",
    ) is None


def test_same_uri_on_different_loan_and_borrower_does_not_prove_scope():
    first = selected()
    second = selected(loan(account="foreign-account"))
    cache = CoverDecisionCache()
    old = publish(cache, first)
    assert cache.selected_token(
        second.scope, revision=1, uri=first.uri,
    ) is None
    token2 = publish(cache, second)
    assert token2.scope != old.scope
    assert cache.selected_token(
        second.scope, revision=1, uri=second.uri,
    ) == token2
    assert cache.selected_token(
        first.scope, revision=1, uri=first.uri,
    ) == old


def test_late_gtk_callback_cannot_resurrect_invalidated_source():
    cache = CoverDecisionCache()
    first = selected()
    old = publish(cache, first)
    trace = CoverLifecycleTrace()
    handle = trace.attach(cache, first.scope)
    try:
        assert cache.selected_token(
            first.scope, revision=1, uri=first.uri,
        ) == old
        cache.invalidate(first.scope, cause="account_epoch_changed")
        assert cache.selected_token(
            first.scope, revision=1, uri=first.uri,
        ) is None
        # The N0 observer also rejects the stale token after invalidation.
        assert not trace.note_media(old, source_cached=True)
        fresh = selected(revision=2)
        new = publish(cache, fresh)
        assert new.generation > old.generation
        assert cache.selected_token(
            first.scope, revision=2, uri=fresh.uri,
        ) == new
    finally:
        handle.cancel()


def test_expired_positive_cache_never_leases_old_texture_or_uri():
    now = [0.0]
    cache = CoverDecisionCache(selected_ttl=2.0, clock=lambda: now[0])
    decision = selected()
    publish(cache, decision)
    now[0] = 2.1
    assert cache.selected_token(
        decision.scope, revision=1, uri=decision.uri,
    ) is None
    assert cache.peek(decision.scope, revision=1) is None


@pytest.mark.parametrize("complete", [True, False])
def test_missing_and_pending_snapshots_cannot_be_claimed_painted(complete):
    current = loan()
    decision = project_exact_loan_cover(
        current, (), revision=4, snapshot_complete=complete,
    )
    assert decision.status in ("missing", "pending")
    cache = CoverDecisionCache()
    publish(cache, decision)
    assert cache.selected_token(
        decision.scope, revision=4, uri="https://any.example/image.jpg",
    ) is None


def test_old_generation_same_revision_cannot_replay_after_new_begin():
    cache = CoverDecisionCache()
    decision = selected()
    old = publish(cache, decision)
    replacement = cache.begin(decision.scope, revision=1)
    assert replacement.generation > old.generation
    assert cache.selected_token(
        decision.scope, revision=1, uri=decision.uri,
    ) is None
    assert cache.publish_resolved(replacement, decision)
    assert cache.selected_token(
        decision.scope, revision=1, uri=decision.uri,
    ) == replacement


def test_untrusted_lease_inputs_are_never_promoted():
    cache = CoverDecisionCache()
    decision = selected()
    publish(cache, decision)
    with pytest.raises(TypeError):
        cache.selected_token("loan", revision=1, uri=decision.uri)
    with pytest.raises(ValueError):
        cache.selected_token(decision.scope, revision=True, uri=decision.uri)
    with pytest.raises(ValueError):
        cache.selected_token(decision.scope, revision=-1, uri=decision.uri)
    for uri in (None, 7, True, {}, ""):
        assert cache.selected_token(
            decision.scope, revision=1, uri=uri,
        ) is None
