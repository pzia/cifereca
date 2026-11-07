"""#4761 P1: Recommendation text field is the SAME local Search field selector.

Headless, deterministic, no network/provider/re-ranking or GTK dependency.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from recommendations import (
    UxRecommendationsView,
    _local_recommendation_field_values,
)


def view_for(query, field="all"):
    view = object.__new__(UxRecommendationsView)
    view._score = NS(get_value=lambda: 0)
    controls = NS(
        filter_text=lambda: query[0],
        selected_sources=lambda: (),
        selected_languages=lambda: (),
        filter_field=lambda: field[0] if type(field) is list else field,
        matches_document=lambda _item: True,
    )
    view._query_controls = controls
    return view


def ranked(**fields):
    return NS(item=fields, score=85, sources=("catalog-A",))


def test_field_filter_only_reads_selected_book_fact_and_is_reversible():
    text = ["astronomie"]
    field = ["subject"]
    view = view_for(text, field)
    astronomy = ranked(
        title="Atlas des mers", author="Alice",
        metadata={"subjects": [{"label": "Astronomie"}], "series": "Planètes"},
    )
    other = ranked(
        title="Astronomie", author="Astronomie",
        metadata={"subjects": ["Géographie"], "series": "Navigation"},
    )
    assert view._matches(astronomy)
    assert not view._matches(other)  # title must not leak into Sujet
    field[0] = "title"
    assert not view._matches(astronomy) and view._matches(other)
    field[0] = "all"
    assert not view._matches(astronomy) and view._matches(other)
    text[0] = "alice"
    assert view._matches(astronomy)
    text[0] = ""
    assert view._matches(astronomy) and view._matches(other)


@pytest.mark.parametrize(("field", "text", "match"), [
    ("author", "deuxieme", True),
    ("subject", "archeologie", True),
    ("series", "royaume", True),
    ("collection", "horizons", True),
    ("language", "fr", True),
    ("isbn", "9780306406157", True),
    ("isbn", "NOT-AN-ISBN", False),
    ("isbn", "987654", False),
    ("other", "royaume", False),
])
def test_only_bounded_explicit_field_projection(field, text, match):
    item = {
        "title": "Livre neutre",
        "authors": ["Deuxième autrice"],
        "metadata": {
            "subjects": [{"value": "Archéologie"}],
            "series": "Royaume oublié",
            "collection": "Horizons",
            "languages": ["fr"],
        },
        "isbn": "9780306406157",
        "record_id": "987654",
    }
    view = view_for([text], field)
    assert view._matches(ranked(**item)) is match


def test_one_character_subject_is_whole_word_not_a_substring():
    view = view_for(["a"], "subject")
    assert view._matches(ranked(metadata={"subjects": ["Sujet A"]}))
    assert not view._matches(ranked(metadata={"subjects": ["Astronomie"]}))


def test_bad_supplier_metadata_and_objects_are_never_coerced():
    class Secret:
        def __str__(self):
            raise AssertionError("provider object stringified")
        def __repr__(self):
            raise AssertionError("provider object rendered")

    safe = {
        "title": "Livre",
        "metadata": {
            "subjects": [Secret(), {"name": Secret()}, "Géologie"],
            "series": Secret(),
            "epub_metadata": {"subjects": [Secret()]},
        },
    }
    assert _local_recommendation_field_values(safe, "subject") == ("Géologie",)
    assert _local_recommendation_field_values(safe, "series") == ()
    assert _local_recommendation_field_values(safe, "other") == ()
    assert not view_for(["secret"], "subject")._matches(ranked(**safe))


def test_field_token_order_independent_and_identity_context_stays_local():
    item = ranked(
        title="Un livre", metadata={"subjects": ["Musique baroque"]},
    )
    view = view_for(["BAROQUE MUSIQUE"], "subject")
    assert view._matches(item)
    assert not view_for(["baroque"], "author")._matches(item)
    # A selected field constrains loaded presentation only, not the
    # recommendation's score, source variants or its provider rights.
    assert item.score == 85 and item.sources == ("catalog-A",)
