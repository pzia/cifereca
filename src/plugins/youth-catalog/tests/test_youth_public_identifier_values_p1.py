"""#1599: cached identifier dictionaries are not automatically public facts.

Tests the two public exits: BookRef navigation and list/detail edition label.
No GTK, providers, files, network or acquisition.
"""
from __future__ import annotations

from youth_page import book_ref, detail_presentation, edition_reference, list_entry
from tests.test_youth_page import youth_book


class Poison:
    def __str__(self):
        raise AssertionError("PRIVATE_ACCOUNT must never be converted")


class SecretText(str):
    pass


def test_unknown_private_identifier_keys_never_reach_public_bookref_or_detail():
    book = youth_book(
        source_id="milan", record_id="public-42",
        identifiers={
            "isbn": "9780306406157",
            "provider": "milan",
            "source_item_id": "catalogue-42",
            "doi": "10.1000/182",
            "auth": "PRIVATE_SIGNED_AUTH",
            "session": "PRIVATE_SESSION",
            "access_token": "PRIVATE_TOKEN",
            "custom": Poison(),
        },
    )
    reference = book_ref(book)
    assert reference.identifiers == {
        "isbn": "9780306406157", "provider": "milan",
        "source_item_id": "catalogue-42", "doi": "10.1000/182",
    }
    entry, detail = list_entry(book), detail_presentation(book)
    assert "ISBN 9780306406157" in entry.meta
    assert "PRIVATE" not in repr((reference, entry, detail))


def test_signed_url_and_non_text_source_coordinates_are_not_edition_evidence():
    book = youth_book(
        record_id="local-public-reference",
        source_id="milan",
        identifiers={
            "isbn": "9780306406158",  # wrong checksum
            "source_item_id": "https://site.example/ed?auth=PRIVATE_TOKEN",
            "provider": SecretText("PRIVATE_PROVIDER"),
            "doi": Poison(),
            "ean": 1234567890123,
        },
    )
    reference, entry, detail = book_ref(book), list_entry(book), detail_presentation(book)
    assert reference.identifiers == {}
    assert edition_reference(book) == (
        "source-reference", "Référence source", "milan:local-public-reference",
    )
    assert "PRIVATE" not in repr((reference, entry, detail))


def test_neither_bidi_nor_numeric_or_oversized_identifiers_become_public_fields():
    book = youth_book(
        source_id="milan", record_id="attested-id",
        identifiers={
            "source_item_id": "PRIVATE\u202eROLE",
            "provider": "milan",
            "isbn": SecretText("9780306406157"),
            "isbn13": "9780306406157",
            "oclc": "A" * 400,
            "issn": {"secret": "PRIVATE"},
            "lccn": "https://site.test/book?session=PRIVATE",
        },
    )
    reference = book_ref(book)
    assert reference.identifiers == {"provider": "milan", "isbn13": "9780306406157"}
    assert "PRIVATE" not in repr(reference)


def test_original_valid_edition_identifiers_remain_unchanged():
    ids = {"isbn": "0-306-40615-2", "provider": "ecole-loisirs", "source_item_id": "p:book-1"}
    book = youth_book(identifiers=ids, record_id="p:book-1")
    reference = book_ref(book)
    assert reference.identifiers == ids
    assert reference.identifiers is not ids
    assert edition_reference(book) == ("isbn", "ISBN", "9780306406157")
