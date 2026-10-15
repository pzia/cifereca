"""#1599: public youth BookRef navigation tolerates malformed cached IDs."""
from collections.abc import Mapping

import pytest

from youth_catalog import YouthBook
from youth_page import book_ref, detail_presentation


class MalformedIdentifiers(Mapping):
    def __iter__(self):
        raise AssertionError("PRIVATE_ID_PROVIDER")

    def __len__(self):
        return 1

    def __getitem__(self, key):
        raise AssertionError("PRIVATE_ID_PROVIDER")


@pytest.mark.parametrize("identifiers", (
    "PRIVATE_TOKEN",
    ["not-a-key-value-pair"],
    14,
    MalformedIdentifiers(),
))
def test_malformed_identifier_envelope_never_crashes_host_navigation(identifiers):
    book = YouthBook(
        source_id="source-public",
        record_id="book-42",
        title="Titre public",
        authors=("Autrice",),
        identifiers=identifiers,
    )
    reference = book_ref(book)
    assert reference.book_id == "book-42"
    assert reference.title == "Titre public"
    assert reference.authors == ("Autrice",)
    assert reference.identifiers == {}
    detail = detail_presentation(book)
    assert detail.title == reference.title
    assert "PRIVATE" not in repr(reference)


def test_attested_edition_coordinates_are_preserved():
    ids = {
        "isbn": "9780306406157",
        "provider": "milan",
        "source_item_id": "catalogue-42",
    }
    book = YouthBook(
        source_id="milan", record_id="catalogue-42", title="Livre",
        authors=("Alice", "Bob"), identifiers=ids,
    )
    reference = book_ref(book)
    assert reference.identifiers == ids
    assert reference.identifiers is not ids
