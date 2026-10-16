"""#1599: list keys, detail and BookRef must not reveal private source IDs.

No GTK or external provider; malformed cache records stay selectable through
synthetic *presentation* keys without inventing an attested edition identity.
"""
from __future__ import annotations

import pytest

from youth_page import _mapped_youth_entries, book_key, book_ref, detail_presentation, edition_reference, list_entry
from tests.test_youth_page import youth_book


class Poison:
    def __str__(self):
        raise AssertionError("PRIVATE_CREDENTIAL must never be stringified")


class SecretText(str):
    pass


@pytest.mark.parametrize("value", (
    Poison(),
    SecretText("PRIVATE_CREDENTIAL"),
    {"PRIVATE_TOKEN": "opaque"},
    "https://entrepot2r.numilog.com/fulfillment/URLLink.acsm?auth=PRIVATE_TOKEN",
    "urn:book?session=PRIVATE_TOKEN",
    "book#PRIVATE_FRAG",
    "book&auth=PRIVATE_TOKEN",
    "auth=PRIVATE_TOKEN",
    "book;session=PRIVATE_SESSION",
    "book%3Fauth%3DPRIVATE_TOKEN",
    "reader@PRIVATE_ACCOUNT",
    "book\\PRIVATE_SECRET",
    "book\u202ePRIVATE_BIDI",
    "book\x00PRIVATE_CONTROL",
    "z" * 513,
    None,
))
def test_invalid_record_ids_do_not_cross_public_row_or_bookref(value):
    book = youth_book(
        record_id=value,
        identifiers={},
        notice_url="",
        friendly_url="",
    )
    row, detail, ref = list_entry(book), detail_presentation(book), book_ref(book)
    assert row.key == "ecole-loisirs:record"
    assert ref.book_id == ""
    assert edition_reference(book) == ("", "", "")
    assert "PRIVATE" not in repr((row, detail, ref))
    assert detail.title == row.title == "Un livre"


def test_unverified_public_record_fallback_is_not_an_attested_source_reference():
    book = youth_book(record_id=Poison(), source_id="milan", identifiers={})
    assert book_ref(book).book_id == ""
    assert list_entry(book).key == "milan:record"
    assert edition_reference(book) == ("", "", "")


@pytest.mark.parametrize("source", (
    Poison(),
    "PRIVATE\u202eSOURCE",
    SecretText("PRIVATE_ACCOUNT"),
    {"PRIVATE": "milan"},
    "https://private.invalid/token",
))
def test_non_public_source_slug_is_never_rendered_in_list_key_or_edition(source):
    book = youth_book(source_id=source, identifiers={}, record_id="book-42")
    row, detail = list_entry(book), detail_presentation(book)
    assert row.key == "source:book-42"
    assert edition_reference(book) == ("", "", "")
    assert "PRIVATE" not in repr((row, detail))
    assert book_ref(book).book_id == "book-42"


def test_colliding_invalid_records_remain_selectable_by_distinct_ui_row_keys():
    books = (
        youth_book(record_id=Poison(), identifiers={}, title="Edition A"),
        youth_book(record_id="record", identifiers={}, title="Edition B"),
        youth_book(record_id=Poison(), identifiers={}, title="Edition C"),
    )
    indexed, entries = _mapped_youth_entries(books)
    assert len(entries) == len(indexed) == 3
    assert len(set(row.key for row in entries)) == 3
    assert entries[1].key == "ecole-loisirs:record:1"
    assert all(indexed[row.key] is book for row, book in zip(entries, books))
    assert all(row.title == book.title for row, book in zip(entries, books))


def test_normal_local_and_unicode_fallback_record_ids_remain_attested():
    for record in ("book-42", "prod:2026/7", "œuvre_42"):
        book = youth_book(record_id=record, identifiers={})
        assert book_ref(book).book_id == record
        assert book_key(book) == f"ecole-loisirs:{record}"
        assert edition_reference(book) == (
            "source-reference", "Référence source", f"ecole-loisirs:{record}"
        )
