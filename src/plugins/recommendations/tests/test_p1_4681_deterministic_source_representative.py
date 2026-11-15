"""#4681: stable and source-qualified representative for identical ISBNs.

A catalogue can return multiple credential-scoped variants for one edition.
Content score ties must not choose an unqualified record just because it
arrived first; the complete variants stay available for explicit selection.
"""
from __future__ import annotations

import pytest

from recommendation_engine import build_profile, candidate_key
from recommendation_engine_fast import rank_candidates_prepared


BOOKS = [{
    "title": "Livre source", "author": "A. Autrice",
    "subjects": ["robotique", "histoire"],
}]
BASE = {
    "isbn": "9780306406157", "title": "Édition candidate",
    "author": "A. Autrice", "subjects": ["robotique"],
}
UNSCOPED = {**BASE, "source_provider": "cat-0", "record_id": "record-u"}
ZETA = {
    **BASE, "source_provider": "cat-z", "source_credential": "credential-z",
    "record_id": "record-z", "docbase": "BOOKS",
    "source_collection": "digital",
}
ALPHA = {
    **BASE, "source_provider": "cat-a", "source_credential": "credential-a",
    "record_id": "record-a", "docbase": "BOOKS",
    "source_collection": "digital",
}


def _rank(rows, *, exploration=0, profile=None):
    return rank_candidates_prepared(
        build_profile(BOOKS) if profile is None else profile,
        (tuple(rows), tuple(reversed(rows))),
        exploration=exploration, limit=3,
    )


def _variants(result):
    return {
        (n.source_provider, n.source_credential, n.docbase,
         n.source_collection, n.record_id, n.isbn)
        for n in result.notices
    }


@pytest.mark.parametrize("rows", [
    (UNSCOPED, ALPHA, ZETA), (ZETA, ALPHA, UNSCOPED),
    (ALPHA, UNSCOPED, ZETA), (ZETA, UNSCOPED, ALPHA),
])
@pytest.mark.parametrize("exploration", [0, 100])
def test_same_isbn_prefers_stable_exact_source_without_losing_variants(
    rows, exploration,
):
    assert len({candidate_key(row) for row in rows}) == 1
    ranked = _rank(rows, exploration=exploration)
    assert len(ranked) == 1
    chosen = ranked[0]
    assert chosen.item is ALPHA
    assert chosen.score > 0 and chosen.query_hits == 2
    assert len(chosen.notices) == 3
    assert _variants(chosen) == {
        ("cat-0", "", "", "", "record-u", "9780306406157"),
        ("cat-z", "credential-z", "BOOKS", "digital", "record-z", "9780306406157"),
        ("cat-a", "credential-a", "BOOKS", "digital", "record-a", "9780306406157"),
    }
    assert set(chosen.sources) == {"cat-0", "cat-a", "cat-z"}
    assert not hasattr(chosen, "executable")


def test_exact_provenance_outranks_unscoped_only_for_equal_scores():
    for rows in ((UNSCOPED, ZETA), (ZETA, UNSCOPED)):
        ranked = _rank(rows)
        assert len(ranked) == 1
        assert ranked[0].item is ZETA
        assert len(ranked[0].notices) == 2


def test_documentary_score_still_precedes_source_representative_tiebreak():
    # The qualified ISBN is identical. A genuinely higher relevance score
    # wins even when the lower-scored source sorts alphabetically first.
    better = {**ZETA, "subjects": ["robotique", "histoire"]}
    worse = {**ALPHA, "subjects": []}
    for rows in ((worse, better), (better, worse)):
        ranked = _rank(rows)
        assert len(ranked) == 1
        assert ranked[0].item is better
        assert len(ranked[0].notices) == 2


def test_distinct_isbn_still_has_distinct_ranked_candidates():
    distinct = {**ALPHA, "isbn": "9780140328721"}
    assert candidate_key(distinct) != candidate_key(ALPHA)
    ranked = _rank((distinct, ZETA, ALPHA))
    assert len(ranked) == 2
    assert {r.item["isbn"] for r in ranked} == {
        "9780306406157", "9780140328721",
    }
    same_edition, = (r for r in ranked if r.item["isbn"] == "9780306406157")
    assert same_edition.item is ALPHA
    assert len(same_edition.notices) == 2
