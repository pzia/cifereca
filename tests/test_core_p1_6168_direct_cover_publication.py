"""P1 #6168: one N1 decision, one exact N2 publication, multiple views."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.book_cover_cache import CoverDecisionCache
from cifereca.core.book_cover_projection import resolve_book_cover
from cifereca.core.book_cover_source_projection import (
    project_exact_loan_cover, project_verified_local_library_cover,
)
from cifereca.core.covers import candidate
from tests.test_core_p1_4979_exact_loan_cover import (
    loan, local_row, remote_row,
)


def _local_placement(current):
    row = local_row(current)
    row["key"] = "placement:exact-" + current.identity_id
    return row


def test_one_exact_core_library_decision_paints_list_and_detail():
    cache = CoverDecisionCache()
    row = _local_placement(loan())
    decision = project_verified_local_library_cover(
        row, expected_identity_id="alice",
        revision=10, snapshot_complete=True,
    )
    assert decision.status == "selected"
    a, b = [], []
    cache.subscribe(decision.scope, a.append)
    cache.subscribe(decision.scope, b.append)
    token = cache.begin(decision.scope, revision=10)
    assert cache.publish_resolved(token, decision)
    assert cache.peek(decision.scope, revision=10) == decision
    assert a[-1].decision == decision and b[-1].decision == decision
    assert a[-1].decision is not b[-1].decision
    assert a[-1].generation == b[-1].generation
    assert [x.kind for x in a] == ["refresh", "resolved"]


def test_loan_supplier_and_verified_placement_publish_same_decision_to_both_views():
    current = loan()
    cache = CoverDecisionCache()
    decision = project_exact_loan_cover(
        current, (local_row(current),), revision=4, snapshot_complete=True,
    )
    assert decision.status == "selected" and decision.tier == "embedded"
    events = []
    cache.subscribe(decision.scope, events.append)
    token = cache.begin(decision.scope, revision=4)
    assert cache.publish_resolved(token, decision)
    assert events[-1].decision == decision
    assert cache.peek(decision.scope, revision=4).uri == decision.uri
    assert current.thumbnail_url is None
    assert not cache.publish_resolved(token, decision)


def test_other_loan_or_account_cannot_replay_same_image():
    current = loan()
    a = project_exact_loan_cover(
        current, (local_row(current),), revision=1, snapshot_complete=True,
    )
    b = project_exact_loan_cover(
        loan(account="other-account"), (), revision=1, snapshot_complete=True,
    )
    cache = CoverDecisionCache()
    token = cache.begin(a.scope, revision=1)
    with pytest.raises(ValueError, match="scope"):
        cache.publish_resolved(token, b)
    assert cache.peek(a.scope, revision=1) is None
    assert cache.publish_resolved(token, a)
    assert cache.peek(b.scope, revision=1) is None


def test_mismatched_revision_fails_without_poisoning_current_slot():
    current = loan()
    decision = project_exact_loan_cover(
        current, (local_row(current),), revision=8, snapshot_complete=True,
    )
    cache = CoverDecisionCache()
    token = cache.begin(decision.scope, revision=9)
    with pytest.raises(ValueError, match="revision"):
        cache.publish_resolved(token, decision)
    next_decision = replace(decision, revision=9)
    assert cache.publish_resolved(token, next_decision)
    assert cache.peek(decision.scope, revision=8) is None


def test_partial_snapshot_stays_pending_and_is_never_negative_proof():
    current = loan()
    pending = project_exact_loan_cover(
        current, (), revision=3, snapshot_complete=False,
    )
    assert pending.status == "pending"
    cache = CoverDecisionCache()
    events = []
    cache.subscribe(pending.scope, events.append)
    token = cache.begin(pending.scope, revision=3)
    assert cache.publish_resolved(token, pending)
    assert cache.peek(pending.scope, revision=3).reason == "partial_snapshot"
    assert events[-1].decision.status == "pending"


def test_stale_worker_after_invalidation_is_not_published_or_notified():
    current = loan()
    decision = project_exact_loan_cover(
        current, (local_row(current),), revision=1, snapshot_complete=True,
    )
    cache = CoverDecisionCache()
    token = cache.begin(decision.scope, revision=1)
    events = []
    cache.subscribe(decision.scope, events.append)
    cache.invalidate(decision.scope, cause="placement_changed")
    assert not cache.publish_resolved(token, decision)
    assert cache.peek(decision.scope, revision=1) is None
    assert [event.kind for event in events] == ["invalidated"]


@pytest.mark.parametrize("bad", [
    dict(schema="forged-v2"),
    dict(status="selected", reason="partial_snapshot"),
    dict(status="invalid"),
    dict(status="missing", uri="https://public.example.org/book.jpg"),
    dict(status="selected", uri="https://public.example.org/cover.jpg?token=secret"),
    dict(status="selected", uri="http://127.0.0.1/admin"),
    dict(status="selected", uri="file:///tmp/foreign.png", tier="origin"),
    dict(status="selected", source="A" * 129),
    dict(candidate_count=True),
])
def test_malformed_precomputed_decision_is_never_cached(bad):
    current = loan()
    valid = project_exact_loan_cover(
        current, (local_row(current),), revision=1, snapshot_complete=True,
    )
    cache = CoverDecisionCache()
    token = cache.begin(valid.scope, revision=1)
    with pytest.raises(ValueError):
        cache.publish_resolved(token, replace(valid, **bad))
    assert cache.peek(valid.scope, revision=1) is None
    assert cache.publish_resolved(token, valid)


def test_same_payload_cannot_publish_again_after_ttl_eviction():
    now = [0.0]
    cache = CoverDecisionCache(selected_ttl=1.0, clock=lambda: now[0])
    decision = project_exact_loan_cover(
        loan(), (local_row(loan()),), revision=1, snapshot_complete=True,
    )
    token = cache.begin(decision.scope, revision=1)
    assert cache.publish_resolved(token, decision)
    now[0] = 2.0
    assert cache.peek(decision.scope, revision=1) is None
    assert not cache.publish_resolved(token, decision)


def test_existing_resolution_and_direct_n1_publication_share_contract():
    current = loan()
    decision = project_exact_loan_cover(
        current, (local_row(current),), revision=5, snapshot_complete=True,
    )
    direct = CoverDecisionCache()
    inferred = CoverDecisionCache()
    assert direct.publish_resolved(
        direct.begin(decision.scope, revision=5), decision,
    )
    alt = candidate(
        decision.uri, tier=decision.tier, source=decision.source,
        evidence_id=decision.evidence_id,
    )
    assert inferred.resolve_and_publish(
        inferred.begin(decision.scope, revision=5), [alt],
        snapshot_complete=True,
    )
    assert direct.peek(decision.scope, revision=5).uri == (
        inferred.peek(decision.scope, revision=5).uri
    )


@pytest.mark.parametrize("field,value", [
    ("revision", True), ("revision", 1.0),
    ("generation", True), ("generation", 1.0),
    ("generation", 0), ("generation", -1),
])
@pytest.mark.parametrize("direct", [True, False])
def test_malformed_token_does_not_consume_valid_slot(field, value, direct):
    cache = CoverDecisionCache()
    valid = project_exact_loan_cover(
        loan(), (local_row(loan()),), revision=1, snapshot_complete=True,
    )
    events = []
    cache.subscribe(valid.scope, events.append)
    token = cache.begin(valid.scope, revision=1)
    malformed = replace(token, **{field: value})
    with pytest.raises(ValueError, match="invalid cover cache token"):
        if direct:
            cache.publish_resolved(malformed, valid)
        else:
            cache.resolve_and_publish(malformed, (), snapshot_complete=True)
    assert cache.peek(valid.scope, revision=1) is None
    assert [event.kind for event in events] == ["refresh"]
    assert all(cache.n1_latency_stats()[f"n1_{status}_count"] == 0
               for status in ("selected", "missing", "pending", "ambiguous"))
    assert cache.publish_resolved(token, valid)
    assert cache.peek(valid.scope, revision=1) == valid
    assert [event.kind for event in events] == ["refresh", "resolved"]


def test_zero_revision_remains_valid_for_initial_snapshot():
    cache = CoverDecisionCache()
    valid = project_exact_loan_cover(
        loan(), (), revision=0, snapshot_complete=False,
    )
    token = cache.begin(valid.scope, revision=0)
    assert cache.publish_resolved(token, valid)
    assert cache.peek(valid.scope, revision=0) == valid
