"""#4980: N2 must revoke a selected lease when exact evidence conflicts.

Synthetic input at the N1/N2 boundary, not an attestation of multi-Artifact
association or an actual GTK paint.
"""
from cifereca.core.book_cover_cache import CoverDecisionCache
from cifereca.core.book_cover_projection import CoverDocumentScope
from cifereca.core.book_cover_trace import CoverLifecycleTrace
from cifereca.core.covers import CoverCandidate


def test_embedded_conflict_revokes_previous_n2_lease_for_both_views():
    scope = CoverDocumentScope(
        kind="placement", identity_id="alice",
        source_key="placement:42", artifact_id="artifact:42",
        placement_id="placement:42",
    )
    first = CoverCandidate(
        "file:///tmp/embedded-a.png", "embedded", "epub", "proof:a", 100,
    )
    second = CoverCandidate(
        "file:///tmp/embedded-b.png", "embedded", "epub", "proof:b", 100,
    )
    fallback = CoverCandidate(
        "https://public.example.org/cover.png", "catalog", "fixture",
        "public:42", 999,
    )
    cache = CoverDecisionCache()
    listed, detailed = [], []
    left = cache.subscribe(scope, listed.append)
    right = cache.subscribe(scope, detailed.append)
    trace = CoverLifecycleTrace()
    handle = trace.attach(cache, scope)
    try:
        old = cache.begin(scope, revision=1)
        assert cache.resolve_and_publish(
            old, [first, fallback], snapshot_complete=True,
        )
        assert cache.selected_token(scope, revision=1, uri=first.url) == old

        current = cache.begin(scope, revision=2)
        assert current.generation > old.generation
        assert cache.resolve_and_publish(
            current, [second, fallback, first], snapshot_complete=True,
        )
        decision = cache.peek(scope, revision=2)
        assert decision.status == "ambiguous"
        assert decision.reason == "conflicting_evidence"
        assert decision.tier == "embedded"
        assert decision.candidate_count == 2
        assert not decision.uri and not decision.source and not decision.evidence_id
        assert cache.peek(scope, revision=1) is None
        for row in (first, second, fallback):
            for revision in (1, 2):
                assert cache.selected_token(
                    scope, revision=revision, uri=row.url,
                ) is None
        assert not trace.note_media(old, source_cached=True)
        assert not cache.resolve_and_publish(
            old, [first], snapshot_complete=True,
        )
        for events in (listed, detailed):
            assert [event.kind for event in events] == [
                "refresh", "resolved", "refresh", "resolved",
            ]
            assert events[-1].decision == decision
            assert events[-1].generation == current.generation
        assert cache.peek(scope, revision=2) == decision
    finally:
        handle.cancel()
        left.cancel()
        right.cancel()
