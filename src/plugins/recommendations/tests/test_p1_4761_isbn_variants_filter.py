"""P1 #4761: ISBN local filter uses original, source-scoped exact notices.

Pure UI projection; no provider search, authentication or rights inference.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from recommendation_engine import CatalogNoticeVariant
from recommendations import (
    UxRecommendationsView,
    _attested_catalogue_isbns,
    _local_ranked_isbns,
)

ISBN_A = "9780306406157"
ISBN_B = "9780140328721"


def variant(source="alpha", isbn=ISBN_A, record="edition-A", *,
            credential="credential-A", docbase="BOOKS"):
    return CatalogNoticeVariant(
        item={"title": "Un même titre"},
        source_provider=source,
        source_credential=credential,
        record_id=record,
        docbase=docbase,
        source_collection="catalog",
        isbn=isbn,
    )


def ranked(*, candidate_isbn=ISBN_B, notices=(), provider="alpha"):
    return NS(
        item={
            "title": "Un même titre",
            "source_provider": provider,
            "isbn": candidate_isbn,
        },
        score=85, sources=("alpha", "beta"),
        notices=notices,
    )


def matches(row, isbn, sources=()):
    view = object.__new__(UxRecommendationsView)
    view._score = NS(get_value=lambda: 0)
    view._query_controls = NS(
        filter_text=lambda: isbn,
        selected_sources=lambda: sources,
        selected_languages=lambda: (),
        filter_field=lambda: "isbn",
        matches_document=lambda _item: True,
    )
    return view._matches(row)


def test_merged_variants_are_scoped_to_active_catalogue_not_candidate_scalar():
    # Candidate-level ISBN B is *not* evidence for catalogue A.
    row = ranked(notices=(
        variant("alpha", ISBN_A, "a"),
        variant("beta", ISBN_B, "b"),
    ))
    assert _local_ranked_isbns(row, ("alpha",)) == (ISBN_A,)
    assert _local_ranked_isbns(row, ("beta",)) == (ISBN_B,)
    assert _local_ranked_isbns(row, ()) == (ISBN_B, ISBN_A)
    assert matches(row, ISBN_A, ("alpha",))
    assert not matches(row, ISBN_B, ("alpha",))
    assert matches(row, ISBN_B, ("beta",))
    assert not matches(row, ISBN_A, ("beta",))
    assert matches(row, ISBN_A)
    assert matches(row, ISBN_B)


def test_variant_only_isbn_matches_even_when_aggregate_has_none():
    row = ranked(candidate_isbn="", notices=(variant(),))
    assert matches(row, ISBN_A)
    assert matches(row, ISBN_A, ("alpha",))
    assert not matches(row, ISBN_A, ("beta",))


def test_bad_variant_cannot_publish_an_isbn_or_cross_sources():
    forged = NS(
        has_source_coordinates=True, isbn=ISBN_A,
        source_provider="alpha", source_credential="credential-A",
        record_id="edition-A", docbase="BOOKS", source_collection="catalog",
    )
    row = ranked(candidate_isbn="", notices=(
        forged,
        variant("alpha", "9780306406158", "bad-checksum"),
        variant("alpha", ISBN_A, "bad-credential", credential=""),
        variant("beta", ISBN_B, "b"),
    ))
    assert _attested_catalogue_isbns(row.notices, selected_sources=("alpha",), limit=32) == ()
    assert not matches(row, ISBN_A, ("alpha",))
    assert not matches(row, ISBN_B, ("alpha",))
    assert matches(row, ISBN_B, ("beta",))


def test_filter_inspects_more_than_four_valid_editions_without_network():
    many = tuple(
        variant("alpha", ISBN_B, f"other-{index}") for index in range(12)
    ) + (variant("alpha", ISBN_A, "exact-12"),)
    row = ranked(candidate_isbn="", notices=many)
    assert _attested_catalogue_isbns(many) == (ISBN_B, ISBN_A)
    assert matches(row, ISBN_A, ("alpha",))


def test_legacy_candidate_without_original_variants_retains_own_isbn():
    row = NS(
        item={"title": "Legacy", "isbn": ISBN_A, "source_provider": "alpha"},
        score=85, sources=("alpha",),
    )
    assert matches(row, ISBN_A)
    assert matches(row, ISBN_A, ("alpha",))
    assert not matches(row, ISBN_A, ("beta",))


def test_wrong_source_and_malformed_notices_never_expand_into_all_catalogues():
    row = ranked(candidate_isbn=ISBN_B, notices=(variant("alpha"),))
    assert not matches(row, ISBN_B, ("alpha",))
    assert not matches(row, ISBN_A, ("beta",))
    row.notices = {ISBN_A: variant()}
    assert not matches(row, ISBN_A, ("alpha",))
    assert not matches(row, ISBN_B, ("beta",))


@pytest.mark.parametrize("bad_sources", [
    ["alpha"],  # untrusted mutable source filter
    ("alpha", object()),
    tuple("x" for _ in range(129)),
])
def test_bad_source_filter_cannot_upgrade_catalogue_variant(bad_sources):
    row = ranked(candidate_isbn="", notices=(variant(),))
    assert _attested_catalogue_isbns(
        row.notices, selected_sources=bad_sources, limit=32,
    ) == ()
