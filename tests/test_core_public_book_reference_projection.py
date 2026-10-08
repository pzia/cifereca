from __future__ import annotations

from cifereca.plugin_api import BookRef, book_ref_from_row, document_links


def test_public_book_ref_preserves_contextual_id_and_source_identity():
    row = {
        "item_id": "book-123", "key": "artifact:sha256:abcdef",
        "title": "Le Roman", "author": "Auteure A ; Auteur B",
        "provider_id": "kobo", "metadata": {"language": "fr", "subjects": ["roman"]},
    }
    book = book_ref_from_row(row)
    assert isinstance(book, BookRef)
    assert book.book_id == "book-123"
    assert book.title == "Le Roman"
    assert book.authors == ("Auteure A", "Auteur B")
    assert book.identifiers == {"provider": "kobo", "source_item_id": "book-123"}
    assert book.metadata["subjects"] == ["roman"]
    assert book.metadata is not row["metadata"]


def test_public_book_ref_extracts_attested_isbn_from_metadata_and_keeps_notice_separate():
    row = {
        "record_id": "ark:/12148/cb123", "source_provider": "bnf",
        "metadata": {
            "isbn": "978-0-306-40615-7",
            "search_result": {"friendly_url": "https://catalogue.bnf.fr/ark:/12148/cb123"},
        },
    }
    book = book_ref_from_row(row)
    assert book.identifiers == {
        "provider": "bnf",
        "source_item_id": "ark:/12148/cb123",
        "isbn": "9780306406157",
    }
    assert [link.url for link in document_links(row)] == [
        "https://catalogue.bnf.fr/ark:/12148/cb123"
    ]


def test_public_book_ref_supports_only_checksum_valid_isbn10_or_isbn13():
    book = book_ref_from_row({"title": "Exemple", "metadata": {"isbn": "0-306-40615-2"}})
    assert book.identifiers == {"isbn": "0306406152"}
    book = book_ref_from_row({"title": "Exemple", "metadata": {"isbn": "9780306406158"}})
    assert book.identifiers == {}
    book = book_ref_from_row({
        "identifiers": {"isbn": ["9780306406158", "9780306406157"]},
    })
    assert book.identifiers == {"isbn": "9780306406157"}


def test_public_book_ref_falls_back_to_canonical_key_without_fabricating_a_provider():
    book = book_ref_from_row({
        "key": "artifact:sha256:123456", "title": "Local",
        "metadata": {"isbn": "invalid", "friendly_url": "https://source.example/"},
    })
    assert book.book_id == "artifact:sha256:123456"
    assert book.identifiers == {}
    assert document_links({"friendly_url": "https://source.example/"}) == ()


def test_public_book_ref_handles_malformed_provider_data_without_crashing():
    for metadata in (None, [], "broken", {"identifiers": [], "search_result": "broken"}):
        book = book_ref_from_row({
            "item_id": "a",
            "provider_id": "paris",
            "authors": ["Auteur A", None, "Auteur A", "Auteur B ; Auteur C"],
            "metadata": metadata,
            "identifiers": {"isbn": object()},
        })
        assert book.book_id == "a"
        assert book.authors == ("Auteur A", "Auteur B", "Auteur C")
        assert book.identifiers == {"provider": "paris", "source_item_id": "a"}
        assert isinstance(book.metadata, dict)


def test_public_book_ref_keeps_isbns_from_nested_search_without_opaque_inference():
    row = {
        "metadata": {
            "search_result": {
                "source_provider": "paris", "title": "Livre du catalogue",
                "isbn13": "9780306406157", "author": "Une personne",
            },
        },
    }
    book = book_ref_from_row(row)
    assert book.book_id == ""
    assert book.title == "Livre du catalogue"
    assert book.authors == ("Une personne",)
    assert book.identifiers == {"provider": "paris", "isbn": "9780306406157"}
