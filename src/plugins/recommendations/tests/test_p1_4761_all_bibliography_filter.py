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


@pytest.mark.parametrize(("field", "value", "secret"), [
    ("title", "https://catalogue.example/book?token=PRIVATE_TITLE_TOKEN",
     "PRIVATE_TITLE_TOKEN"),
    ("author", "https://catalogue.example/person?token=PRIVATE_AUTHOR_TOKEN",
     "PRIVATE_AUTHOR_TOKEN"),
    ("title", "Notice PRIVATE_BEARER_TOKEN", "PRIVATE_BEARER_TOKEN"),
    ("author", "contact@example.invalid", "contact@example.invalid"),
    ("title", "/home/profile/private-book-title", "private-book-title"),
])
def test_all_never_indexes_url_or_secret_like_title_and_author(
    field, value, secret,
):
    # A provider can return a syntactically valid string which is suitable
    # for a cautious UI label but not for the local searchable bibliography.
    # The other, genuinely attested fields remain searchable.
    row = ranked(**{field: value})
    assert not matches(row, secret)
    assert matches(row, "Botanique")
    assert matches(row, "Nébuleuse")


def test_explicit_title_author_filters_remain_independent_of_all_index():
    # The change is deliberately restricted to the broad 'Tous' index:
    # specific-field selection keeps its established UI contract.
    row = ranked(title="Catalogue ordinaire", author="Autrice locale")
    assert matches(row, "Catalogue", field="title")
    assert matches(row, "Autrice", field="author")
    assert matches(row, "Catalogue autrice", field="all")


def test_url_like_subject_is_not_indexed_by_default_all():
    row = ranked(metadata={
        "subjects": [
            "https://cdn.invalid/cover?token=PRIVATE_SUBJECT_TOKEN",
            "Botanique abyssale",
        ],
    })
    assert not matches(row, "PRIVATE_SUBJECT_TOKEN")
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


@pytest.mark.parametrize("query", [
    "botanique autrice",
    "AUTRICE BOTANIQUE",
    "de botanique",
    "chroniques autrice",
    "livre nebuleuse",
    "9780306406157 botanique",
])
def test_all_terms_match_independently_across_attested_fields(query):
    assert matches(ranked(), query)


@pytest.mark.parametrize(("query", "expected"), [
    ('"botanique abyssale"', True),
    ('"botanique abyssale" autrice', True),
    ('autrice "botanique abyssale"', True),
    ('"botanique" "autrice"', True),
    ('"botanique autrice"', False),
    ('"livre nebuleuse"', False),
    ('"botanique introuvable"', False),
    ("botanique introuvable", False),
    ('"botanique abyssale" introuvable', False),
])
def test_all_quoted_phrases_stay_within_one_attested_field(query, expected):
    assert matches(ranked(), query) is expected


def test_all_stopwords_do_not_select_every_unrelated_book():
    row = ranked()
    assert not matches(row, "du")
    assert not matches(row, "de le des")
    assert not matches(row, '"..."')
    assert not matches(row, "---")
    assert matches(row, 'de botanique')


def test_all_never_joins_two_different_fields_to_forge_a_quoted_phrase():
    row = ranked(title="Planetes", metadata={"subjects": ["Difficiles"]})
    assert matches(row, "Planetes difficiles")
    assert not matches(row, '"Planetes difficiles"')


def test_quoted_phrase_and_terms_preserve_source_exact_isbn_guard():
    row = ranked()
    assert matches(row, '"botanique abyssale" 9780306406157', sources=("alpha",))
    assert not matches(row, '"botanique abyssale" 9780140328721', sources=("alpha",))
    assert matches(row, '"botanique abyssale" 9780140328721', sources=("beta",))


def test_explicit_subject_field_still_requires_all_terms_not_phrase_parser():
    row = ranked()
    assert matches(row, "abyssale botanique", field="subject")
    assert not matches(row, "autrice botanique", field="subject")
