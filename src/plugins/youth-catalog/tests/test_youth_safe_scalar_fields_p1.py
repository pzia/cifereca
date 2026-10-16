"""#1599: metadata restored from a Youth cache is not automatically public.

Book list and public host detail must agree, never coerce poisoned field
objects, invent an attested publication year or propagate bidi controls.
"""
from __future__ import annotations

import pytest

from youth_page import detail_presentation, list_entry
from tests.test_youth_page import youth_book


class Poison:
    def __str__(self):
        raise AssertionError("PRIVATE_USER_PASSWORD cannot be stringified")


class PoisonText(str):
    pass


def _metadata(detail):
    return {
        f.label: f.value
        for section in detail.sections
        for f in section.fields
    }


@pytest.mark.parametrize("raw", (
    Poison(),
    {"PRIVATE_SECRET": "fr"},
    PoisonText("PRIVATE_TOKEN"),
    "fr\u202ePRIVATE_BIDI",
    "fr\x00PRIVATE_CONTROL",
    42,
    None,
))
def test_language_never_coerces_provider_objects_or_controlled_values(raw):
    book = youth_book(language=raw)
    row, detail = list_entry(book), detail_presentation(book)
    assert "Langue" not in _metadata(detail)
    assert "PRIVATE" not in repr((row, detail))
    assert " · fr" not in row.meta


@pytest.mark.parametrize("field", ("book_type", "collection", "description"))
def test_poisoned_scalar_metadata_is_excluded_from_public_detail(field):
    for raw in (Poison(), PoisonText("PRIVATE"), {"PRIVATE_TOKEN": "x"}, "a\u202ePRIVATE"):
        book = youth_book(**{field: raw})
        detail = detail_presentation(book)
        assert "PRIVATE" not in repr(detail)
        assert all(
            f.value not in (raw,) for section in detail.sections for f in section.fields
        )


@pytest.mark.parametrize("year", (
    Poison(), PoisonText("2022"), True, 2022.3, float("inf"), -1,
    0, 999, 2200, "PRIVATE_YEAR", "2023\u202e", {"PRIVATE": "2023"},
))
def test_invalid_publication_year_is_never_displayed_as_attested(year):
    detail = detail_presentation(youth_book(published_year=year))
    assert "Première publication" not in _metadata(detail)
    assert "PRIVATE" not in repr(detail)


def test_valid_scalar_fields_preserve_list_detail_and_year_identity():
    book = youth_book(
        language="  fr-FR  ",
        book_type="   Album jeunesse   ",
        collection="  Petite   Bibliothèque  ",
        description=" Un grand voyage   vers  les étoiles  ",
        published_year=1999,
    )
    row, detail = list_entry(book), detail_presentation(book)
    metadata = _metadata(detail)
    assert "fr-FR" in row.meta
    assert metadata["Langue"] == "fr-FR"
    assert metadata["Type"] == "Album jeunesse"
    assert metadata["Collection"] == "Petite Bibliothèque"
    assert metadata["Résumé éditeur"] == "Un grand voyage vers les étoiles"
    assert metadata["Première publication"] == "1999"


def test_legacy_year_text_can_be_attested_but_not_implicitly_cast():
    detail = detail_presentation(youth_book(published_year="2024"))
    assert _metadata(detail)["Première publication"] == "2024"


def test_overlong_scalar_fields_are_dropped_without_truncating_into_a_false_fact():
    book = youth_book(
        language="f" * 33,
        book_type="c" * 181,
        collection="p" * 241,
        description="d" * 4001,
    )
    row, detail = list_entry(book), detail_presentation(book)
    for label in ("Langue", "Type", "Collection", "Résumé éditeur"):
        assert label not in _metadata(detail)
    assert "f" * 33 not in row.meta
