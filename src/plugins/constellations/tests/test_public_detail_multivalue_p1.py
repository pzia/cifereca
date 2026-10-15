"""#1599: Constellations uses public typed details for bibliographic values.

Presentation only: no GTK, external links fetched, acquisitions or mutations.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import BookRef, UiBookDetailPresentation
from constellations_page import constellation_detail_presentation


@pytest.mark.parametrize("authors,sources", (
    ((), ()),
    (("Alice",), ("bnf",)),
    (("Alice", "Bob", "Carol"), ("bnf", "paris", "kobo")),
))
def test_public_detail_preserves_all_author_and_source_values(authors, sources):
    node = NS(
        book=BookRef("edition-id", "Book title", authors),
        sources=sources,
        availability=("externe",),
        url="https://example.org/catalogue/edition-id",
    )
    detail = constellation_detail_presentation(node)
    assert isinstance(detail, UiBookDetailPresentation)
    assert detail.title == "Book title"
    assert detail.subtitle == ", ".join(authors)
    assert detail.badges == ("externe",)
    assert len(detail.links) == 1
    assert detail.links[0].url == "https://example.org/catalogue/edition-id"
    assert detail.actions == ()
    sections = {section.id: section for section in detail.sections}
    if authors:
        assert [f.value for f in sections["bibliography"].fields] == list(authors)
        assert [f.id for f in sections["bibliography"].fields] == [
            f"author-{i}" for i in range(len(authors))
        ]
        assert all(f.label == "Auteur" and f.kind == "author"
                   for f in sections["bibliography"].fields)
    else:
        assert "bibliography" not in sections
    if sources:
        assert [f.value for f in sections["provenance"].fields] == list(sources)
        assert [f.id for f in sections["provenance"].fields] == [
            f"source-{i}" for i in range(len(sources))
        ]
        assert all(f.label == "Source" and f.kind == "copy"
                   for f in sections["provenance"].fields)
    else:
        assert "provenance" not in sections


def test_no_invented_bibliography_when_absent_and_distinct_same_title_editions():
    first = NS(
        book=BookRef("edition-a", "Same title", ()),
        sources=(), availability=(), url="",
    )
    second = NS(
        book=BookRef("edition-b", "Same title", ("Bob",)),
        sources=("paris",), availability=("empruntable",), url="",
    )
    a = constellation_detail_presentation(first)
    b = constellation_detail_presentation(second)
    assert a.title == b.title == "Same title"
    assert a.subtitle == "" and a.sections == () and a.links == ()
    assert b.subtitle == "Bob"
    assert tuple(section.id for section in b.sections) == (
        "bibliography", "provenance",
    )
    # A graph access label is descriptive, not a direct borrow capability.
    assert b.badges == ("empruntable",)
    assert b.actions == ()
    assert all(not field.id.startswith("acquisition") for section in b.sections
               for field in section.fields)


def test_whitespace_and_missing_values_are_not_exported_as_fake_authors():
    detail = constellation_detail_presentation(NS(
        book=BookRef("edition-a", "Title", ("Alice", " ", "Bob")),
        sources=("", "paris", " "),
        availability=(), url="",
    ))
    assert [field.value for sec in detail.sections for field in sec.fields] == [
        "Alice", "Bob", "paris",
    ]
    assert detail.subtitle == "Alice, Bob"


def test_merged_constellation_deduplicates_identical_provenance_in_order():
    """A graph can attest one provider through multiple edges, not two sources."""
    node = NS(
        book=BookRef("edition-a", "Title", ("Alice", "Alice", "Bob")),
        sources=("BnF", "Paris", "BnF", "Kobo", "Paris", "Bearer SECRET\u202e"),
        availability=("externe",),
        url="",
    )
    detail = constellation_detail_presentation(node)
    sections = {section.id: section for section in detail.sections}
    assert [(f.id, f.value, f.kind) for f in sections["provenance"].fields] == [
        ("source-0", "BnF", "copy"),
        ("source-1", "Paris", "copy"),
        ("source-2", "Kobo", "copy"),
    ]
    # Distinct author entries can denote distinct credits; this patch does
    # not change their meaning or graph/book identity.
    assert [f.value for f in sections["bibliography"].fields] == [
        "Alice", "Alice", "Bob",
    ]
    assert detail.actions == () and detail.links == ()
    assert "SECRET" not in str(detail)


def test_repeated_only_source_keeps_single_provenance_section_field():
    detail = constellation_detail_presentation(NS(
        book=BookRef("edition-b", "Title", ()),
        sources=("Paris", "Paris", "Paris"),
        availability=(), url="",
    ))
    assert len(detail.sections) == 1
    assert detail.sections[0].id == "provenance"
    assert [(f.id, f.value) for f in detail.sections[0].fields] == [
        ("source-0", "Paris"),
    ]
