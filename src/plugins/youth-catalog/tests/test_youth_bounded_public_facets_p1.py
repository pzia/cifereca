"""#1599: malformed Youth metadata must not manufacture public bibliographic facts.

No GTK, providers, files, accounts, network or acquisitions are involved.
"""
from __future__ import annotations

from youth_page import detail_presentation, list_entry
from tests.test_youth_page import youth_book


class Poison:
    def __str__(self):
        raise AssertionError("Bearer PRIVATE_TOKEN must not be stringified")


class EvilText(str):
    pass


def public_fields(detail, *, label):
    return [f.value for sec in detail.sections for f in sec.fields if f.label == label]


def test_mapping_keys_are_not_authors_or_public_bibliographic_values():
    private = "Bearer_PRIVATE_CREDENTIAL"
    book = youth_book(
        authors={private: "Alice"},
        provenance={private: "Paris"},
        formats={private: "EPUB"},
        themes={private: "science"},
        publishers={private: "Éditeur"},
        bibliography_sources={private: "Bibliothèque"},
        age_ranges=(),
    )
    entry, detail = list_entry(book), detail_presentation(book)
    assert entry.subtitle == detail.subtitle == ""
    assert "Source" not in [f.label for sec in detail.sections for f in sec.fields]
    for kind in ("Auteur", "Source", "Format", "Thème", "Éditeur", "Source bibliographique"):
        assert public_fields(detail, label=kind) == []
    assert private not in entry.meta
    assert private not in str(detail)


def test_safe_scalar_and_multivalued_fields_preserve_list_and_detail_meaning():
    book = youth_book(
        authors="Alice",
        provenance="Paris",
        formats="EPUB",
        themes=("Science", " Aventure "),
        publishers=("Édition A", "Édition B"),
        bibliography_sources=("BnF", "OpenLibrary"),
        age_ranges=(),
    )
    entry = list_entry(book)
    detail = detail_presentation(book)
    assert entry.subtitle == detail.subtitle == "Alice"
    assert "Paris" in entry.meta and "EPUB" in entry.meta
    assert public_fields(detail, label="Auteur") == ["Alice"]
    assert public_fields(detail, label="Source") == ["Paris"]
    assert public_fields(detail, label="Format") == ["EPUB"]
    assert public_fields(detail, label="Thème") == ["Science", "Aventure"]
    assert public_fields(detail, label="Éditeur") == ["Édition A", "Édition B"]
    assert public_fields(detail, label="Source bibliographique") == ["BnF", "OpenLibrary"]


def test_cached_poison_bidi_and_str_subclasses_never_become_public_values():
    book = youth_book(
        authors=EvilText("PRIVATE_AUTHOR"),
        provenance=("Paris", Poison(), "PRIVATE_SOURCE\u202e", EvilText("SECRET")),
        formats=("epub", Poison(), "PRIVATE\nDOWNLOAD"),
        themes=("nature", EvilText("Bearer_PRIVATE"), Poison()),
        publishers=(Poison(), "Publique"),
        bibliography_sources=("BnF", {"PRIVATE": "Source"}, "PRIVATE\x7fKEY"),
        age_ranges=(),
    )
    entry, detail = list_entry(book), detail_presentation(book)
    assert entry.subtitle == detail.subtitle == ""
    assert public_fields(detail, label="Source") == ["Paris"]
    assert public_fields(detail, label="Format") == ["epub"]
    assert public_fields(detail, label="Thème") == ["nature"]
    assert public_fields(detail, label="Éditeur") == ["Publique"]
    assert public_fields(detail, label="Source bibliographique") == ["BnF"]
    assert "PRIVATE" not in repr((entry, detail))


def test_large_facet_sources_are_bounded_and_ordered_without_truncating_strings():
    book = youth_book(
        authors=tuple(f"Author {i}" for i in range(80)),
        provenance=tuple(f"Source {i}" for i in range(80)),
        formats=tuple(f"format-{i}" for i in range(80)),
        themes=tuple(f"topic-{i}" for i in range(80)),
        publishers=tuple(f"publisher-{i}" for i in range(80)),
        bibliography_sources=tuple(f"reference-{i}" for i in range(80)),
        age_ranges=(),
    )
    entry, detail = list_entry(book), detail_presentation(book)
    for label in ("Auteur", "Source", "Format", "Thème", "Éditeur", "Source bibliographique"):
        assert len(public_fields(detail, label=label)) == 32
    assert public_fields(detail, label="Source")[-1] == "Source 31"
    assert public_fields(detail, label="Thème")[0] == "topic-0"
    assert entry.subtitle == detail.subtitle == ", ".join(f"Author {i}" for i in range(32))
    assert "Source 2" in entry.meta and "Source 3" not in entry.meta
