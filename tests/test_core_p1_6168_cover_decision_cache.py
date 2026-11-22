"""#6168 N2: shared decision cache, explicit events and stale completion guards."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from itertools import chain

import pytest

from cifereca.core.book_cover_cache import CoverDecisionCache
from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.covers import CoverCandidate

URL = "https://public.example.org/book.png"
ALT = "https://public.example.org/changed.png"


def loan(**changes):
    attrs = dict(
        kind="loan", identity_id="alice", provider_id="biblioaccess",
        credential_id="card-a", account_id="", loan_id="loan-42",
        source_key="loan-source:exact-42",
    )
    attrs.update(changes)
    return CoverDocumentScope(**attrs)


def candidate(url=URL):
    return CoverCandidate(url, "origin", "provider", "loan:42", 100)


def completed(cache, scope, *, revision=1, rows=None, complete=True):
    token = cache.begin(scope, revision=revision)
    assert cache.resolve_and_publish(
        token, [candidate()] if rows is None else rows,
        snapshot_complete=complete,
    )
    return token


def test_list_and_detail_subscribers_observe_one_decision_without_extra_fetch():
    cache = CoverDecisionCache()
    exact = loan()
    list_events, detail_events = [], []
    left = cache.subscribe(exact, list_events.append)
    right = cache.subscribe(exact, detail_events.append)
    completed(cache, exact)
    listed = cache.peek(exact, revision=1)
    detail = cache.peek(exact, revision=1)
    assert listed == detail and listed is not detail
    assert listed.status == "selected" and listed.uri == URL
    assert [x.kind for x in list_events] == ["refresh", "resolved"]
    assert [x.kind for x in detail_events] == ["refresh", "resolved"]
    assert detail_events[-1].decision == listed
    assert detail_events[-1].decision is not listed
    assert list_events[-1].scope == exact
    assert list_events[-1].generation == detail_events[-1].generation
    left.cancel()
    right.cancel()
    right.cancel()
    assert cache.stats()["subscribers"] == 0


def test_source_revision_and_observation_epoch_reject_stale_completion():
    cache = CoverDecisionCache()
    exact = loan()
    first = cache.begin(exact, revision=1)
    second = cache.begin(exact, revision=2)
    assert not cache.resolve_and_publish(first, [candidate()], snapshot_complete=True)
    assert cache.resolve_and_publish(second, [candidate(ALT)], snapshot_complete=True)
    assert cache.peek(exact, revision=1) is None
    assert cache.peek(exact, revision=2).uri == ALT
    with pytest.raises(ValueError, match="stale"):
        cache.begin(exact, revision=1)


@pytest.mark.parametrize("cause", [
    "catalogue_changed", "account_epoch_changed", "identity_changed",
    "artifact_changed", "placement_changed", "origin_link_changed",
    "loan_changed", "entitlement_changed", "evidence_changed",
])
def test_invalidation_revokes_in_flight_work_and_notifies_both_views(cause):
    cache = CoverDecisionCache()
    exact = loan()
    seen = []
    cache.subscribe(exact, seen.append)
    token = cache.begin(exact, revision=12)
    cache.invalidate(exact, cause=cause)
    assert cache.peek(exact, revision=12) is None
    assert not cache.resolve_and_publish(token, [candidate()], snapshot_complete=True)
    assert seen[-1].kind == "invalidated"
    assert seen[-1].cause == cause and seen[-1].decision is None
    assert seen[-1].generation > seen[-2].generation
    next_token = cache.begin(exact, revision=13)
    assert cache.resolve_and_publish(next_token, [candidate()], snapshot_complete=True)
    assert cache.peek(exact, revision=13).status == "selected"


def test_invalidation_is_scoped_by_identity_borrower_credential_and_loan():
    cache = CoverDecisionCache()
    alice, bob = loan(), loan(identity_id="bob")
    second_card = loan(credential_id="card-b")
    second_loan = loan(loan_id="loan-43")
    other_borrower = loan(account_id="linked-1")
    scopes = (alice, bob, second_card, second_loan, other_borrower)
    for scope in scopes:
        completed(cache, scope)
    cache.invalidate(alice, cause="account_epoch_changed")
    assert cache.peek(alice, revision=1) is None
    for scope in scopes[1:]:
        assert cache.peek(scope, revision=1).uri == URL


def test_negative_pending_and_ambiguous_ttls_are_distinct_and_bounded():
    now = [10.0]
    cache = CoverDecisionCache(
        selected_ttl=100.0, missing_ttl=10.0,
        ambiguous_ttl=4.0, pending_ttl=1.0,
        clock=lambda: now[0],
    )
    scopes = [loan(loan_id=str(i)) for i in range(4)]
    completed(cache, scopes[0], rows=[])
    completed(cache, scopes[1], rows=[], complete=False)
    completed(cache, scopes[2], rows=[
        candidate(), candidate(ALT),
    ])
    completed(cache, scopes[3])
    assert [cache.peek(scope, revision=1).status for scope in scopes] == [
        "missing", "pending", "ambiguous", "selected",
    ]
    now[0] = 11.1
    assert cache.peek(scopes[1], revision=1) is None
    assert cache.peek(scopes[0], revision=1).status == "missing"
    now[0] = 14.1
    assert cache.peek(scopes[2], revision=1) is None
    now[0] = 20.1
    assert cache.peek(scopes[0], revision=1) is None
    assert cache.peek(scopes[3], revision=1).uri == URL


def test_untrusted_uri_is_never_persisted_or_notified():
    cache = CoverDecisionCache()
    exact = loan()
    seen = []
    cache.subscribe(exact, seen.append)
    completed(cache, exact, rows=[
        CoverCandidate("https://public.example.org/secret?token=abc", "catalog", "bad"),
    ])
    hit = cache.peek(exact, revision=1)
    assert hit.status == "missing"
    assert hit.uri == ""
    assert seen[-1].decision == hit
    assert "token=abc" not in repr(hit)


def test_one_broken_subscriber_does_not_block_second():
    cache = CoverDecisionCache()
    exact = loan()
    seen = []
    def broken(_event):
        raise RuntimeError("simulated failed UI repaint")
    cache.subscribe(exact, broken)
    cache.subscribe(exact, seen.append)
    completed(cache, exact)
    assert [event.kind for event in seen] == ["refresh", "resolved"]


def test_pending_from_iterable_failure_is_not_missing_and_never_half_committed():
    cache = CoverDecisionCache()
    scope = loan()
    def failed():
        yield candidate()
        raise RuntimeError("provider incomplete")
    completed(cache, scope, rows=failed())
    assert cache.peek(scope, revision=1).status == "pending"


def test_invalidation_batch_is_atomic_on_invalid_input_or_source_failure():
    cache = CoverDecisionCache()
    scopes = [loan(loan_id="a"), loan(loan_id="b")]
    for scope in scopes:
        completed(cache, scope)
    with pytest.raises(TypeError):
        cache.invalidate_many(chain(scopes, [object()]), cause="loan_changed")
    for scope in scopes:
        assert cache.peek(scope, revision=1) is not None
    def broken():
        yield scopes[0]
        raise RuntimeError("source failed")
    with pytest.raises(RuntimeError):
        cache.invalidate_many(broken(), cause="loan_changed")
    for scope in scopes:
        assert cache.peek(scope, revision=1) is not None
    assert cache.invalidate_many([*scopes, scopes[0]], cause="loan_changed") == 2
    assert all(cache.peek(scope, revision=1) is None for scope in scopes)


def test_bounded_eviction_cannot_reactivate_stale_token():
    cache = CoverDecisionCache(max_scopes=2)
    first_scope = loan(loan_id="first")
    old = cache.begin(first_scope, revision=1)
    completed(cache, loan(loan_id="second"))
    completed(cache, loan(loan_id="third"))
    assert cache.peek(first_scope, revision=1) is None
    assert not cache.resolve_and_publish(old, [candidate()], snapshot_complete=True)
    fresh = cache.begin(first_scope, revision=1)
    assert fresh.generation > old.generation
    assert cache.resolve_and_publish(fresh, [candidate()], snapshot_complete=True)


def test_subscriber_budget_unsubscribe_and_no_duplicate_notifications():
    cache = CoverDecisionCache(max_subscribers=1)
    target = loan()
    seen = []
    handle = cache.subscribe(target, seen.append)
    with pytest.raises(ValueError):
        cache.subscribe(target, seen.append)
    handle.cancel()
    completed(cache, target)
    assert seen == []
    h2 = cache.subscribe(target, seen.append)
    cache.invalidate(target, cause="loan_changed")
    assert len(seen) == 1
    h2.cancel()


@pytest.mark.parametrize("bad", [
    0, -1, float("inf"), float("nan"), 86401, True, "30",
])
def test_expiry_must_be_finite_positive_bounded(bad):
    with pytest.raises(ValueError):
        CoverDecisionCache(missing_ttl=bad)


def test_invalid_cause_and_revisions_are_fail_closed():
    cache = CoverDecisionCache()
    with pytest.raises(ValueError):
        cache.invalidate(loan(), cause="unknown")
    with pytest.raises(ValueError):
        cache.invalidate_many([], cause="unknown")
    with pytest.raises(ValueError):
        cache.begin(loan(), revision=True)


def test_no_work_without_exact_source_scope_or_token():
    cache = CoverDecisionCache()
    with pytest.raises(TypeError):
        cache.begin("loan", revision=1)
    with pytest.raises(TypeError):
        cache.resolve_and_publish(object(), [candidate()], snapshot_complete=True)
    with pytest.raises(TypeError):
        cache.subscribe(loan(), object())


def test_reentrant_callbacks_have_monotonic_event_sequences_for_gtk():
    cache = CoverDecisionCache()
    scope = loan()
    observed = []
    def reentrant(event):
        if event.kind == "refresh":
            cache.invalidate(scope, cause="evidence_changed")
    cache.subscribe(scope, reentrant)
    cache.subscribe(scope, observed.append)
    stale = cache.begin(scope, revision=1)
    # A callback may publish a newer event before another listener sees the
    # original refresh. Consumers discard events with lower sequence values.
    assert [event.kind for event in observed] == ["invalidated", "refresh"]
    assert observed[0].sequence > observed[1].sequence
    assert not cache.resolve_and_publish(
        stale, [candidate()], snapshot_complete=True,
    )


def test_snapshot_can_be_published_only_once_even_after_cache_expiry():
    now = [0.0]
    cache = CoverDecisionCache(clock=lambda: now[0], selected_ttl=1)
    scope = loan()
    token = cache.begin(scope, revision=1)
    assert cache.resolve_and_publish(token, [candidate()], snapshot_complete=True)
    assert not cache.resolve_and_publish(
        token, [candidate(ALT)], snapshot_complete=True,
    )
    assert cache.peek(scope, revision=1).uri == URL
    now[0] = 2.0
    assert cache.peek(scope, revision=1) is None
    assert not cache.resolve_and_publish(
        token, [candidate(ALT)], snapshot_complete=True,
    )
    assert cache.peek(scope, revision=1) is None
    second = cache.begin(scope, revision=2)
    assert cache.resolve_and_publish(
        second, [candidate(ALT)], snapshot_complete=True,
    )
    assert cache.peek(scope, revision=2).uri == ALT


def test_concurrent_observation_cannot_publish_after_invalidation():
    from threading import Event, Thread

    cache = CoverDecisionCache()
    scope = loan()
    token = cache.begin(scope, revision=1)
    entered, resumed = Event(), Event()
    outcomes = []

    def delayed():
        entered.set()
        assert resumed.wait(5)
        yield candidate()

    thread = Thread(
        target=lambda: outcomes.append(
            cache.resolve_and_publish(
                token, delayed(), snapshot_complete=True,
            )
        ),
        daemon=True,
    )
    thread.start()
    try:
        assert entered.wait(5)
        cache.invalidate(scope, cause="placement_changed")
    finally:
        resumed.set()
        thread.join(5)
    assert not thread.is_alive()
    assert outcomes == [False]
    assert cache.peek(scope, revision=1) is None


@pytest.mark.parametrize("operation", [
    lambda cache, scope: cache.begin(scope, revision=1),
    lambda cache, scope: cache.peek(scope, revision=1),
    lambda cache, scope: cache.observe(scope, revision=1),
    lambda cache, scope: cache.selected_token(scope, revision=1, uri=URL),
    lambda cache, scope: cache.invalidate(scope, cause="loan_changed"),
    lambda cache, scope: cache.invalidate_many((scope,), cause="loan_changed"),
    lambda cache, scope: cache.subscribe(scope, lambda _change: None),
])
def test_mutated_loan_scope_is_refused_before_n2_map_access(operation):
    cache = CoverDecisionCache()
    safe = loan()
    completed(cache, safe)
    hostile = loan(loan_id="other-loan")
    object.__setattr__(hostile, "credential_id", "")
    with pytest.raises(ValueError, match="loan cover requires exact borrower"):
        operation(cache, hostile)
    assert cache.peek(safe, revision=1).uri == URL


def test_mutated_scope_never_invokes_foreign_hash_or_equality_in_n2():
    class Hostile:
        def __hash__(self):
            raise AssertionError("foreign hash must not run")
        def __eq__(self, _other):
            raise AssertionError("foreign equality must not run")

    cache = CoverDecisionCache()
    scope = loan()
    object.__setattr__(scope, "kind", Hostile())
    with pytest.raises(ValueError, match="invalid cover scope kind"):
        cache.begin(scope, revision=1)
    scope = loan()
    object.__setattr__(scope, "source_key", Hostile())
    with pytest.raises(ValueError, match="invalid cover scope field"):
        cache.peek(scope, revision=1)
    assert cache.stats()["scopes"] == 0


def test_mutated_token_and_resolved_scope_cannot_publish_to_n2():
    from cifereca.core.book_cover_projection import ResolvedBookCover

    cache = CoverDecisionCache()
    exact = loan()
    token = cache.begin(exact, revision=1)
    other = loan()
    object.__setattr__(other, "account_id", None)
    forged = ResolvedBookCover(
        other, 1, "missing", "complete_no_admissible_cover",
    )
    with pytest.raises(ValueError, match="loan cover requires exact borrower"):
        cache.publish_resolved(token, forged)
    assert cache.peek(exact, revision=1) is None

    object.__setattr__(token.scope, "loan_id", "")
    with pytest.raises(ValueError, match="loan cover requires exact borrower"):
        cache.resolve_and_publish(token, [candidate()], snapshot_complete=True)


def test_valid_mutation_of_admitted_producer_scope_cannot_rekey_n2():
    """The caller's nominally frozen scope is not the N2 dictionary key."""
    cache = CoverDecisionCache()
    source = loan()
    events = []
    subscription = cache.subscribe(source, events.append)
    token = cache.begin(source, revision=7)
    assert token.scope == source and token.scope is not source

    # A legal alternate loan still passes __post_init__: validation alone
    # cannot protect a dictionary keyed by the producer-owned object.
    object.__setattr__(source, "loan_id", "other-legal-loan")
    assert token.scope == loan()
    assert cache.resolve_and_publish(
        token, [candidate()], snapshot_complete=True,
    )
    assert cache.peek(loan(), revision=7).uri == URL
    assert cache.peek(source, revision=7) is None
    assert [e.kind for e in events] == ["refresh", "resolved"]
    assert all(e.scope == loan() for e in events)
    assert cache.stats()["scopes"] == 1
    subscription.cancel()
    assert cache.stats()["subscribers"] == 0


def test_mutating_returned_token_scope_cannot_corrupt_n2_key():
    """N2 token provenance is not the stored dictionary-key object."""
    cache = CoverDecisionCache()
    scope = loan()
    old = cache.begin(scope, revision=1)
    object.__setattr__(old.scope, "loan_id", "legally-other-loan")
    assert old.scope != scope
    # An old token may be refused; it must not poison the original cache key.
    next_token = cache.begin(scope, revision=2)
    assert cache.resolve_and_publish(
        next_token, [candidate()], snapshot_complete=True,
    )
    assert cache.peek(scope, revision=2).uri == URL
    assert cache.peek(old.scope, revision=1) is None
    assert cache.stats()["scopes"] == 1
    assert not cache.resolve_and_publish(
        old, [candidate(ALT)], snapshot_complete=True,
    )


def test_mutating_one_listener_event_cannot_rekey_other_n2_subscribers():
    cache = CoverDecisionCache()
    scope = loan()
    events = []

    def mutate_event(event):
        # Valid fields, not a type failure: would corrupt a shared dict key.
        object.__setattr__(event.scope, "loan_id", "another-book")

    cache.subscribe(scope, mutate_event)
    cache.subscribe(scope, events.append)
    completed(cache, scope)
    assert cache.peek(scope, revision=1).uri == URL
    assert [event.kind for event in events] == ["refresh", "resolved"]
    assert all(event.scope == scope for event in events)
    assert cache.stats()["scopes"] == 1



def test_read_leases_cannot_mutate_stored_n2_cas_token():
    """A valid mutation of a returned lease must not corrupt internal CAS."""
    cache = CoverDecisionCache()
    scope = loan()
    producer = cache.begin(scope, revision=1)
    object.__setattr__(producer.scope, "loan_id", "other-valid-loan")
    assert cache.resolve_and_publish(
        cache.begin(scope, revision=2), [candidate()], snapshot_complete=True,
    )
    observed, decision = cache.observe(scope, revision=2)
    assert decision.uri == URL
    selected = cache.selected_token(scope, revision=2, uri=URL)
    assert selected is not None
    assert observed is not selected
    object.__setattr__(observed.scope, "loan_id", "other-valid-loan")
    object.__setattr__(selected.scope, "loan_id", "third-valid-loan")
    assert cache.observe(scope, revision=2)[0].scope == scope
    assert cache.selected_token(scope, revision=2, uri=URL).scope == scope
    assert cache.peek(scope, revision=2).uri == URL
    assert cache.stats()["scopes"] == 1
    cache.invalidate(scope, cause="loan_changed")
    assert cache.peek(scope, revision=2) is None


def test_mutated_admitted_n1_decision_cannot_rewrite_n2_evidence():
    """A valid producer mutation must not change the selected N2 URI/scope."""
    cache = CoverDecisionCache()
    scope = loan()
    producer = cache.begin(scope, revision=3)
    decision = cache.resolve_n1(
        scope, [candidate()], revision=3, snapshot_complete=True,
    )
    events = []
    cache.subscribe(scope, events.append)
    assert cache.publish_resolved(producer, decision)
    object.__setattr__(decision, "uri", ALT)
    object.__setattr__(decision.scope, "loan_id", "another-valid-loan")
    original_scope = loan()
    hit = cache.peek(original_scope, revision=3)
    assert hit is not None and hit.uri == URL and hit.scope == original_scope
    assert cache.selected_token(original_scope, revision=3, uri=URL) == producer
    assert cache.selected_token(original_scope, revision=3, uri=ALT) is None
    assert events[-1].decision.uri == URL
    assert events[-1].decision.scope == original_scope
    assert cache.stats()["scopes"] == 1


def test_n2_reader_and_notification_cannot_mutate_other_reader_decisions():
    """Every read/event lease is value-equal but independently owned."""
    cache = CoverDecisionCache()
    scope = loan()
    later_events = []

    def hostile_listener(event):
        if event.decision is not None:
            object.__setattr__(event.decision, "uri", ALT)
            object.__setattr__(event.decision.scope, "loan_id", "other-valid-loan")

    cache.subscribe(scope, hostile_listener)
    cache.subscribe(scope, later_events.append)
    completed(cache, scope, revision=6)
    assert later_events[-1].decision.uri == URL
    assert later_events[-1].decision.scope == scope

    first = cache.peek(scope, revision=6)
    second = cache.peek(scope, revision=6)
    assert first == second and first is not second
    assert first.scope is not second.scope
    object.__setattr__(first, "uri", ALT)
    object.__setattr__(first.scope, "loan_id", "other-valid-loan")
    observed_token, observed = cache.observe(scope, revision=6)
    assert observed.uri == URL and observed.scope == scope
    object.__setattr__(observed, "uri", ALT)
    object.__setattr__(observed.scope, "loan_id", "third-valid-loan")
    assert cache.peek(scope, revision=6).uri == URL
    assert cache.selected_token(scope, revision=6, uri=URL) == observed_token
    assert cache.selected_token(scope, revision=6, uri=ALT) is None
    assert cache.stats()["scopes"] == 1
