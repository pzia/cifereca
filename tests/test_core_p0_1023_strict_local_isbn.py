"""Explicit ISBN search uses only complete, validated book identifiers."""
import pytest

from cifereca.core.search import filter_loaded_results


@pytest.mark.parametrize("query", (
    "9780306406157", "978-0-306-40615-7", "0306406152", "0-306-40615-2",
))
def test_exact_isbn_alias_matches_only_explicit_bibliographic_fields(query):
    rows = [
        {"title": "ID opaque", "item_id": "9780306406157"},
        {"title": "ISBN explicite", "metadata": {"isbn": "0-306-40615-2"}},
        {"title": "Autre édition", "metadata": {"isbns": ["9780140328721"]}},
        {"title": "ISBN au niveau ligne", "isbn": "9780306406157"},
        {"title": "ID imbriqué", "metadata": {"isbn": {"value": "9780306406157"}}},
        {"title": "C sans preuve", "source": "notice_c_public",
         "metadata": {"isbn": "9780306406157"}},
        {"title": "Champ mixte invalide",
         "metadata": {"isbns": ["9780306406157", "9780306406158"]}},
    ]
    assert [row["title"] for row in filter_loaded_results(rows, query, field="isbn")] == [
        "ISBN explicite", "ISBN au niveau ligne",
    ]


@pytest.mark.parametrize("query", (
    "978030", "030640615", "9780306406158", "4006381333931",
    "9780306406157 9780140328721", "ISBN 9780306406157",
))
def test_invalid_or_partial_isbn_query_cannot_match(query):
    row = {"title": "Livre", "item_id": "4006381333931",
           "metadata": {"isbn": "9780306406157", "isbns": ["4006381333931"]}}
    assert filter_loaded_results([row], query, field="isbn") == []


def test_general_search_preserves_identifier_lookup_and_empty_isbn_filter():
    row = {"title": "Livre", "item_id": "9780306406157"}
    assert filter_loaded_results([row], "9780306406157", field="all") == [row]
    assert filter_loaded_results([row], "", field="isbn") == [row]
    assert filter_loaded_results([row], "9780306406157", field="isbn") == []
