"""P1 #4761: 'Tous' mirrors Search's public bibliographic scope locally.

Headless contract: no provider request, rank recomputation, LLM or rights.
ISBNs remain source-exact even when several catalogue notices are merged.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from recommendation_engine import CatalogNoticeVariant
from recommendations import UxRecommendationsView

ISBN_A = "9780306406157"
ISBN_B = "9780140328721"


def notice(source: str, isbn: str):
    return CatalogNoticeVariant(
        item={"title": "Livre ordinaire"},
        source_provider=source, source_credential="credential-" + source,
        record_id="edition-" + source, docbase="BOOKS",
        source_collection="catalog", isbn=isbn,
    )


def ranked(**changes):
    item = {
        "title": "Livre ordinaire",
        "author": "Autrice locale",
        "source_provider": "alpha",
        "isbn": ISBN_B,
        "publisher": "Éditions Nébuleuse",
        "item_id": "PRIVATE_DO_NOT_INDEX",
        "metadata": {
            "subjects": ["Botanique abyssale"],
            "series": "Chroniques d'Andromède",
            "collection": "Mémoires stellaires",
            "private_marker": "TOKEN_NEVER_INDEX",
            "language": "de",
        },
    }
    item.update(changes)
    return NS(
        item=item, sources=("alpha", "beta"), score=85,
        notices=(notice("alpha", ISBN_A), notice("beta", ISBN_B)),
    )


def matches(row, text, *, sources=(), field="all"):
    view = object.__new__(UxRecommendationsView)
    view._score = NS(get_value=lambda: 0)
    view._query_controls = NS(
        filter_text=lambda: text,
        selected_sources=lambda: sources,
        selected_languages=lambda: (),
        filter_field=lambda: field,
        matches_document=lambda _item: True,
    )
    return view._matches(row)


@pytest.mark.parametrize("query", [
    "Livre ordinaire", "Autrice locale", "Botanique", "Andromède",
    "stellaires", "Nébuleuse", "9780306406157", "9780140328721",
])
def test_all_uses_attested_already_loaded_bibliography(query):
    assert matches(ranked(), query)


def test_isbn_of_other_catalogue_cannot_enter_selected_source_all():
    row = ranked()
    assert matches(row, ISBN_A, sources=("alpha",))
    assert not matches(row, ISBN_B, sources=("alpha",))
    assert matches(row, ISBN_B, sources=("beta",))
    assert not matches(row, ISBN_A, sources=("beta",))
    assert matches(row, ISBN_B)
    assert matches(row, ISBN_A)


@pytest.mark.parametrize("secret", [
    "PRIVATE_DO_NOT_INDEX", "TOKEN_NEVER_INDEX",
    "credential-alpha", "edition-alpha",
])
def test_all_never_indexes_identifiers_credentials_or_private_metadata(secret):
    assert not matches(ranked(), secret)


def test_url_like_publisher_is_not_indexed_as_a_bibliographic_name():
    row = ranked(publisher="https://private.example/loan?token=PRIVATE_TOKEN")
    assert not matches(row, "PRIVATE_TOKEN")
    assert not matches(row, "private.example")
    assert matches(row, "Botanique")


def test_all_rejects_untrusted_opaque_metadata_without_string_coercion():
    class Secret:
        def __str__(self):
            raise AssertionError("PRIVATE_BEARER")

        def __repr__(self):
            raise AssertionError("PRIVATE_BEARER")

    row = ranked(metadata={
        "subjects": [Secret(), "Botanique abyssale"],
        "series": Secret(),
        "collection": "Mémoires stellaires",
    })
    assert matches(row, "Botanique")
    assert not matches(row, "PRIVATE_BEARER")


def test_all_filter_keeps_bounded_result_and_does_not_change_rank_or_search():
    row = ranked()
    row.item["metadata"]["subjects"] = ["Sujet " + str(i) for i in range(10000)]
    observed = []
    view = object.__new__(UxRecommendationsView)
    view._score = NS(get_value=lambda: observed.append("score") or 0)
    view._query_controls = NS(
        filter_text=lambda: "Sujet 000",
        selected_sources=lambda: (),
        selected_languages=lambda: (),
        filter_field=lambda: "all",
        matches_document=lambda _item: True,
    )
    # A query beyond the bounded public indexing window is not implicitly
    # interpreted as access to the entire untrusted 10k-entry payload.
    assert view._matches(row) is False
    assert observed == ["score"]
    assert row.score == 85
    assert len(row.item["metadata"]["subjects"]) == 10000


def test_other_explicit_fields_keep_their_own_filter_semantics():
    row = ranked()
    assert matches(row, "Botanique", field="subject")
    assert not matches(row, "Botanique", field="title")
    assert matches(row, "Chroniques", field="series")
    assert not matches(row, "Chroniques", field="collection")
    assert matches(row, ISBN_A, field="isbn", sources=("alpha",))
    assert not matches(row, ISBN_B, field="isbn", sources=("alpha",))
