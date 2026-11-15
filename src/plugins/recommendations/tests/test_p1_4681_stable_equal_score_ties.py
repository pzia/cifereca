"""#4681: equal-score recommendation ranking is stable across catalog order.

Two distinct ISBN editions can have the same title/author/themes and identical
RRF scores. Neither their order nor the MMR seed should depend on provider
response timing; the exact notice variants must remain separate.
"""
from __future__ import annotations

from recommendation_engine import build_profile, candidate_key
from recommendation_engine_fast import (
    rank_candidates_fast, rank_candidates_prepared,
)


BOOKS = [{
    "title": "Livre possédé", "author": "A. Autrice",
    "subjects": ["science", "géographie"],
}]

COMMON = {
    "title": "Le même titre", "author": "A. Autrice",
    "subjects": ["science"],
    "description": "Géographie imaginaire",
    "source_provider": "catalogue",
    "source_collection": "digital",
    "docbase": "BOOKS",
}
EDITION_A = {
    **COMMON, "isbn": "9780140328721", "record_id": "edition-A",
    "source_credential": "public-A",
}
EDITION_B = {
    **COMMON, "isbn": "9780306406157", "record_id": "edition-B",
    "source_credential": "public-B",
}


def _result(first, second, *, exploration, cache=None):
    profile = build_profile(BOOKS)
    return rank_candidates_prepared(
        profile, ((first, second), (second, first)),
        limit=2, exploration=exploration,
        score_cache=cache,
    )


def _edition_rows(rows):
    return tuple((
        row.item["isbn"], row.score, row.reasons,
        tuple((notice.source_credential, notice.record_id, notice.isbn)
              for notice in row.notices),
    ) for row in rows)


def test_equal_content_and_rrf_have_stable_documentary_tie_break():
    assert candidate_key(EDITION_A) != candidate_key(EDITION_B)
    for exploration in (0, 25, 100):
        forward = _result(EDITION_A, EDITION_B, exploration=exploration)
        reversed_catalog = _result(
            EDITION_B, EDITION_A, exploration=exploration,
        )
        assert len(forward) == len(reversed_catalog) == 2
        assert all(row.query_hits == 2 for row in forward)
        assert forward[0].score == forward[1].score
        assert _edition_rows(forward) == _edition_rows(reversed_catalog)
        assert [row.item["isbn"] for row in forward] == [
            "9780140328721", "9780306406157",
        ]
        assert all(len(row.notices) == 1 for row in forward)


def test_stable_ties_with_shared_score_cache_and_swapped_provider_orders():
    profile = build_profile(BOOKS)
    cache = {}
    a = rank_candidates_prepared(
        profile, ((EDITION_B, EDITION_A), (EDITION_A, EDITION_B)),
        exploration=100, score_cache=cache,
    )
    b = rank_candidates_prepared(
        profile, ((EDITION_A, EDITION_B), (EDITION_B, EDITION_A)),
        exploration=100, score_cache=cache,
    )
    assert _edition_rows(a) == _edition_rows(b)
    assert len(cache) == 2
    assert all(row.score > 0 for row in a)
    assert all(not hasattr(row, "executable") for row in a)


def test_fast_public_api_uses_same_stable_tiebreak_for_distinct_editions():
    for rows in (((EDITION_A, EDITION_B), (EDITION_B, EDITION_A)),
                 ((EDITION_B, EDITION_A), (EDITION_A, EDITION_B))):
        ranked = rank_candidates_fast(
            BOOKS, rows, limit=2, exploration=0,
        )
        assert [row.item["isbn"] for row in ranked] == [
            "9780140328721", "9780306406157",
        ]
        assert {(row.notices[0].record_id, row.notices[0].source_credential)
                for row in ranked} == {
            ("edition-A", "public-A"), ("edition-B", "public-B"),
        }
