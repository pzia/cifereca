"""#1599: public detail projects bibliographic facets from its precise record.

No GTK, HTTP, LLM or library mutation. The graph may have merged nodes;
none of its inferred availability, mood, chronology or rights enter fields.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import BookRef
from constellations_page import constellation_detail_presentation
from constellation_view_model import node_from_row


def detail(row):
    return constellation_detail_presentation(node_from_row(row))


def facets(presentation):
    sections = [s for s in presentation.sections if s.id == "attested-metadata"]
    return sections[0].fields if sections else ()


def test_explicit_isbn_language_publisher_series_formats_subjects_are_visible():
    result = detail({
        "book_id": "edition-a",
        "title": "Public novel",
        "authors": ("Author A", "Author B"),
        "identifiers": {"isbn": "9780306406157"},
        "metadata": {
            "language": "FR", "publisher": "Éditions Sincères",
            "series": ("Série One", "Série Two"),
            "formats": ("EPUB", "PDF"),
            "subjects": ("Amitié", "Voyage"),
        },
    })
    assert [field.id for field in facets(result)] == [
        "isbn", "language", "publisher",
        "series-0", "series-1",
        "format-0", "format-1", "subject-0", "subject-1",
    ]
    assert [(f.label, f.value, f.kind) for f in facets(result)[:3]] == [
        ("ISBN", "9780306406157", "isbn"),
        ("Langue", "fr", "language"),
        ("Éditeur", "éditions sincères", "all"),
    ]
    assert result.subtitle == "Author A, Author B"
    assert [s.id for s in result.sections] == [
        "bibliography", "attested-metadata", "provenance",
    ] or [s.id for s in result.sections] == [
        "bibliography", "attested-metadata",
    ]
    assert result.actions == ()


def test_nested_bibliography_evidence_is_projected_only_from_this_record():
    a = detail({
        "book_id": "source-a", "title": "Same title",
        "metadata": {"search_result": {
            "language": "en", "publisher": "Source A", "subjects": ["A"],
        }},
    })
    b = detail({
        "book_id": "source-b", "title": "Same title",
        "metadata": {"search_result": {
            "language": "fr", "publisher": "Source B", "subjects": ["B"],
        }},
    })
    assert a.title == b.title
    assert [f.value for f in facets(a)] == ["en", "source a", "a"]
    assert [f.value for f in facets(b)] == ["fr", "source b", "b"]


def test_no_metadata_never_invents_isbn_publisher_or_language():
    result = detail({"book_id": "edition-a", "title": "Only title"})
    assert facets(result) == ()
    assert result.actions == ()


def test_inferred_graph_attributes_do_not_become_attested_bibliography():
    result = detail({
        "book_id": "edition-a", "title": "Book",
        "year": "2025", "page_count": 540, "mood": "sad",
        "owned": True, "revision_id": "PRIVATE_REVISION",
        "download_url": "https://private.example.org/acquisition?token=PRIVATE",
    })
    assert facets(result) == ()
    assert result.actions == ()
    assert "possédé" not in result.badges
    assert "PRIVATE" not in str(result)


@pytest.mark.parametrize("isbn", (
    "9780306406150",  # Invalid check digit.
    "9780306406157\u202e",  # Invalid trailing bidi.
    "PRIVATE_PERSONAL_ISBN",
))
def test_invalid_isbn_is_not_promoted_as_verified_public_identifier(isbn):
    result = detail({
        "book_id": "edition-a", "title": "Book",
        "identifiers": {"isbn": isbn},
    })
    assert all(f.id != "isbn" for f in facets(result))


def test_long_or_control_bearing_metadata_is_never_projected():
    result = detail({
        "book_id": "edition-a", "title": "Book",
        "metadata": {
            "publisher": "Bearer PRIVATE_SECRET\u202e",
            "language": "fr",
            "subjects": ["private-" + "x" * 200, "Safe subject"],
        },
    })
    values = [field.value for field in facets(result)]
    assert "fr" in values and "safe subject" in values
    assert all("PRIVATE" not in value and "\u202e" not in value for value in values)


def test_bounded_subject_facets_keep_stable_order():
    result = detail({
        "book_id": "edition-a", "title": "Book",
        "subjects": [f"topic-{i}" for i in range(30)],
    })
    assert [f.value for f in facets(result)] == [
        f"topic-{i}" for i in range(8)
    ]


def test_malformed_metadata_does_not_break_public_base_detail():
    class Poisoned(dict):
        def get(self, *_args):
            raise RuntimeError("Bearer PRIVATE_KEY")

    result = constellation_detail_presentation(NS(
        book=BookRef("edition-a", "Book", ("Author",)),
        sources=("Public",), availability=("externe",), url="",
        raw=Poisoned(),
    ))
    assert facets(result) == ()
    assert result.subtitle == "Author"
    assert result.actions == ()
    assert "PRIVATE" not in str(result)
