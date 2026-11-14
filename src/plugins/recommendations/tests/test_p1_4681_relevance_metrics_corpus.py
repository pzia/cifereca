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
from recommendation_engine_fast import rank_candidates_prepared


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
