"""#4681 — deterministic relevance and diversification evidence, no supplier I/O.

These *synthetic* relevance labels express one controlled user preference.
They are not real user feedback, provider entitlements or live recommendation
quality metrics. No Core schema, GTK or network calls are involved.
"""
from __future__ import annotations

from math import log2

from recommendation_engine import (
    ExplicitReadingSignal, build_profile, is_already_present,
)
from recommendation_engine_fast import rank_candidates_prepared, score_candidate_cached


def _library(key, author, theme, *, identity="alice"):
    return {
        "key": key, "identity_id": identity,
        "title": "Livre source " + key, "author": author,
        "metadata": {"subjects": [theme]},
        "state": "remote_loan_active",
        "source_provider": "fictional-provider",
        # Source, borrowing, and ownership are never implicit preferences.
    }


def _offer(key, author, theme, *, provider="public-catalogue", credential="public"):
    return {
        "record_id": key, "docbase": "BOOKS",
        "title": "Notice " + key, "author": author,
        "subjects": [theme], "source_provider": provider,
        "source_credential": credential, "source_collection": "public",
        "downloadable": False, "owned": False,
    }


BOOKS = [
    _library("rouge-1", "Autrice Rouge", "robotique"),
    _library("rouge-2", "Autrice Rouge", "robotique"),
    _library("bleu-1", "Auteur Bleu", "astronomie"),
]
OFFERS = [
    _offer("rouge-c1", "Autrice Rouge", "robotique"),
    _offer("rouge-c2", "Autrice Rouge", "robotique"),
    _offer("bleu-c1", "Auteur Bleu", "astronomie"),
    _offer("bleu-c2", "Auteur Bleu", "astronomie"),
]

# Explicit editorial evaluation labels, never projected from provider data.
GRADES = {"rouge-c1": 0, "rouge-c2": 0, "bleu-c1": 3, "bleu-c2": 3}


def _rank(*, signals=(), exploration=0, books=BOOKS, offers=OFFERS):
    profile = build_profile(
        books, identity_id="alice", explicit_signals=signals,
    )
    # Identical, frozen catalogue observations for both ranking policies.
    return rank_candidates_prepared(
        profile, [offers, offers], limit=4, exploration=exploration,
    )


def _ids(ranked):
    return [row.item["record_id"] for row in ranked]


def _ndcg_at(ranked, k):
    """Normalized graded relevance for a frozen *synthetic* editorial corpus."""
    def dcg(grades):
        return sum((2**grade - 1) / log2(i + 2)
                   for i, grade in enumerate(grades))
    observed = [GRADES[row.item["record_id"]] for row in ranked[:k]]
    ideal = sorted(GRADES.values(), reverse=True)[:k]
    return dcg(observed) / dcg(ideal)


def _author_coverage_at(ranked, k):
    return len({row.item["author"] for row in ranked[:k]})


def _favored():
    return (
        ExplicitReadingSignal("alice", "rouge-1", "not_interested"),
        ExplicitReadingSignal("alice", "rouge-2", "not_interested"),
        ExplicitReadingSignal("alice", "bleu-1", "liked"),
    )


def test_explicit_preferences_improve_ndcg_at_two_on_frozen_relevance_labels():
    neutral = _rank()
    preferred = _rank(signals=_favored())
    assert len(neutral) == len(preferred) == 4
    assert set(_ids(neutral)) == set(_ids(preferred)) == set(GRADES)
    assert set(_ids(neutral[:2])) == {"rouge-c1", "rouge-c2"}
    assert set(_ids(preferred[:2])) == {"bleu-c1", "bleu-c2"}

    # These are numerical regression metrics, not an assertion of live quality.
    assert _ndcg_at(neutral, 2) == 0.0
    assert _ndcg_at(preferred, 2) == 1.0
    assert _ndcg_at(preferred, 4) > _ndcg_at(neutral, 4)
    assert _rank(signals=()) == neutral  # neutral mode is stable/reversible

    for rows in (neutral, preferred):
        assert all(row.query_hits == 2 for row in rows)
        assert all(row.reasons for row in rows)
        assert all(not hasattr(row, "executable") for row in rows)
        assert all(not row.item["downloadable"] and not row.item["owned"]
                   for row in rows)
        assert {
            (notice.source_provider, notice.source_credential,
             notice.docbase, notice.source_collection, notice.record_id)
            for row in rows for notice in row.notices
        } == {
            ("public-catalogue", "public", "BOOKS", "public", key)
            for key in GRADES
        }


def test_mmr_exploration_increases_top_two_author_diversity_without_new_rights():
    relevance = _rank(exploration=0)
    diversified = _rank(exploration=100)
    assert _author_coverage_at(relevance, 2) == 1
    assert _author_coverage_at(diversified, 2) == 2
    assert _ids(diversified)[0] == _ids(relevance)[0]
    assert set(_ids(diversified)) == set(_ids(relevance))
    assert all(row.query_hits == 2 for row in diversified)
    assert _rank(exploration=0) == relevance


def test_owned_edition_stays_excluded_in_all_modes_and_no_false_second_right():
    owned = _library("owned", "Auteur Bleu", "astronomie")
    owned["isbn"] = "9780306406157"  # checksum-valid exact edition
    duplicate = _offer("owned-offer", "Auteur Bleu", "astronomie")
    duplicate["isbn"] = "9780306406157"
    assert is_already_present(build_profile([owned]), duplicate)

    extended = [*OFFERS, duplicate]
    for signals in ((), _favored()):
        profile = build_profile(
            [*BOOKS, owned], identity_id="alice", explicit_signals=signals)
        rows = rank_candidates_prepared(
            profile, [extended, extended], limit=5, exploration=50)
        assert "owned-offer" not in _ids(rows)
        assert set(_ids(rows)) == set(GRADES)
        assert all(not row.item["downloadable"] and not row.item["owned"]
                   for row in rows)


def test_same_published_corpus_does_not_apply_foreign_profile_feedback():
    foreign = _library("bleu-1", "Auteur Bleu", "astronomie", identity="bob")
    for books in (BOOKS, [*BOOKS, foreign]):
        neutral = _rank(books=books)
        # A matching key belonging to a different identity cannot be selected
        # as if it were Alice's exact preference target.
        irrelevant = (
            ExplicitReadingSignal("alice", "foreign-only", "liked"),
        )
        assert _rank(books=books, signals=irrelevant) == neutral


def test_shared_score_cache_cannot_replay_another_profile_rationale():
    """Reusing a dict between two borrowers must never replay Alice's score."""
    alice = build_profile([_library("a", "Autrice Rouge", "robotique")])
    bob = build_profile([_library("b", "Auteur Bleu", "astronomie")])
    candidate = _offer("unique", "Autrice Rouge", "robotique")
    expected_a = score_candidate_cached(alice, candidate, {})
    expected_b = score_candidate_cached(bob, candidate, {})
    assert expected_a != expected_b

    shared = {}
    assert score_candidate_cached(alice, candidate, shared) == expected_a
    assert score_candidate_cached(bob, candidate, shared) == expected_b
    assert score_candidate_cached(alice, candidate, shared) == expected_a

    # The full ranking API must also remain scoped across the shared cache.
    offers = (candidate,)
    for profile in (alice, bob, alice):
        expected = rank_candidates_prepared(
            profile, (offers, offers), exploration=0, score_cache={},
        )
        observed = rank_candidates_prepared(
            profile, (offers, offers), exploration=0, score_cache=shared,
        )
        assert observed == expected


def test_shared_score_cache_revokes_explicit_preference_after_feedback_change():
    """Liked -> not_interested -> liked are distinct immutable snapshots."""
    book = _library("reading-key", "Autrice Rouge", "robotique")
    # Two neutral books from another author keep the normalization stable
    # while the explicit weight of the target book changes.
    books = [
        book,
        _library("blue-1", "Auteur Bleu", "astronomie"),
        _library("blue-2", "Auteur Bleu", "astronomie"),
    ]
    candidate = _offer("another-edition", "Autrice Rouge", "robotique")
    liked = build_profile(
        books, identity_id="alice",
        explicit_signals=(ExplicitReadingSignal("alice", "reading-key", "liked"),),
    )
    disliked = build_profile(
        books, identity_id="alice",
        explicit_signals=(ExplicitReadingSignal("alice", "reading-key", "not_interested"),),
    )
    baseline = build_profile(books, identity_id="alice")
    shared = {}
    liked_result = score_candidate_cached(liked, candidate, shared)
    disliked_result = score_candidate_cached(disliked, candidate, shared)
    baseline_result = score_candidate_cached(baseline, candidate, shared)
    assert liked_result[0] > baseline_result[0] > disliked_result[0]
    assert score_candidate_cached(liked, candidate, shared) == liked_result
    assert score_candidate_cached(disliked, candidate, shared) == disliked_result


def test_shared_score_cache_cannot_bypass_normal_ownership_exclusion():
    """A score computed after a prior ownership check cannot bypass that check."""
    book = _library("source", "Autrice Rouge", "robotique")
    profile = build_profile([book])
    owned = {
        "title": book["title"], "author": book["author"],
        "subjects": ["robotique"],
    }
    assert is_already_present(profile, owned)
    shared = {}
    admitted = score_candidate_cached(profile, owned, shared, known_absent=True)
    guarded = score_candidate_cached(profile, owned, shared, known_absent=False)
    assert admitted[0] > 0
    assert guarded == (0.0, ())
    assert score_candidate_cached(profile, owned, shared, known_absent=True) == admitted


def test_reused_content_cache_keeps_identical_description_editions_distinct():
    """Same author/title/theme, but a different ISBN is not an owned edition."""
    book = _library("owned", "Autrice Rouge", "robotique")
    book["isbn"] = "9780306406157"
    profile = build_profile([book])
    owned = _offer("owned-edition", "Autrice Rouge", "robotique")
    owned["isbn"] = "9780306406157"
    different = _offer("other-edition", "Autrice Rouge", "robotique")
    different["isbn"] = "9780140328721"
    assert is_already_present(profile, owned)
    assert not is_already_present(profile, different)
    assert score_candidate_cached(profile, different, {})[0] > 0

    for notices in ((different, owned), (owned, different)):
        shared = {}
        results = [score_candidate_cached(profile, item, shared) for item in notices]
        assert results[notices.index(owned)] == (0.0, ())
        assert results[notices.index(different)][0] > 0
        assert score_candidate_cached(profile, owned, shared) == (0.0, ())
        assert score_candidate_cached(profile, different, shared)[0] > 0


def test_reused_content_cache_still_checks_legacy_title_pair_scope():
    """Adding a source scope must not reuse a fuzzy-title ownership result."""
    book = _library("existing", "Autrice Rouge", "robotique")
    profile = build_profile([book])
    unscoped = {
        "title": book["title"], "author": book["author"],
        "subjects": ["robotique"],
    }
    scoped = dict(unscoped, source_credential="a-specific-credential")
    assert is_already_present(profile, unscoped)
    assert not is_already_present(profile, scoped)
    assert score_candidate_cached(profile, scoped, {})[0] > 0

    shared = {}
    assert score_candidate_cached(profile, scoped, shared)[0] > 0
    assert score_candidate_cached(profile, unscoped, shared) == (0.0, ())
    assert score_candidate_cached(profile, scoped, shared)[0] > 0


def test_edition_rebound_after_cached_score_revokes_positive_scoring():
    """Cache entries cannot authorize stale scoring after changing an ISBN."""
    book = _library("existing", "Autrice Rouge", "robotique")
    book["isbn"] = "9780306406157"
    profile = build_profile([book])
    candidate = _offer("candidate", "Autrice Rouge", "robotique")
    candidate["isbn"] = "9780140328721"
    shared = {}
    assert score_candidate_cached(profile, candidate, shared)[0] > 0
    candidate["isbn"] = "9780306406157"
    assert is_already_present(profile, candidate)
    assert score_candidate_cached(profile, candidate, shared) == (0.0, ())
    candidate["isbn"] = "9780140328721"
    assert score_candidate_cached(profile, candidate, shared)[0] > 0
