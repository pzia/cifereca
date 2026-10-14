"""#1599: Constellations details never surface signed or private notice links.

No GTK, HTTP, library mutations, account lookup or acquisition.
"""
from __future__ import annotations

import pytest

from constellation_view_model import node_from_row, public_url


@pytest.mark.parametrize("url", (
    "file:///home/PRIVATE_ACCOUNT/book.epub",
    "javascript:PRIVATE_TOKEN",
    "https://internal.local/PRIVATE_BOOK",
    "http://localhost/PRIVATE_BOOK",
    "http://127.0.0.1/PRIVATE_BOOK",
    "http://10.0.0.1/book",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/PRIVATE_BOOK",
    "https://intranet.internal/books",
    "http://printer.lan/book",
    "https://private.home.arpa/book",
    "https://user:PRIVATE_PASSWORD@example.org/book",
    "https://example.org/book?token=PRIVATE_SIGNED",
    "https://example.org/book#PRIVATE_FRAGMENT",
    "https://example.org:8443/book",
    "https://example.org/book\nPRIVATE_SECRET",
    "https://example.org\\@attacker.example.org/book",
    "https://[malformed/book",
    "https://example.org/%0Abook?auth=PRIVATE_TOKEN",
    "https://example.org/" + "x" * 2100,
    "https://example.org/book\tPRIVATE_TOKEN",
))
def test_unsafe_catalogue_url_never_becomes_detail_link(url):
    assert public_url(url) == ""
    node = node_from_row({
        "book_id": "ed1", "title": "Public work",
        "friendly_url": url,
    })
    assert node.url == ""
    assert node.book.title == "Public work"


@pytest.mark.parametrize("url", (
    "https://example.org/books/123",
    "http://example.org/books/456",
    "https://example.org:443/catalogue/edition",
))
def test_only_plain_external_notice_urls_are_exposed(url):
    assert public_url(url) == url
    assert node_from_row({"book_id": "ed1", "title": "Title", "friendly_url": url}).url == url


@pytest.mark.parametrize("metadata", (
    {"friendly_url": "https://example.org/books/safe"},
    {"search_result": {"friendly_url": "https://example.org/books/safe"}},
))
def test_invalid_preferred_link_does_not_hide_independent_public_notice(metadata):
    node = node_from_row({
        "book_id": "ed1", "title": "Same title",
        "friendly_url": "https://example.org/download?token=PRIVATE_LICENSE",
        "metadata": metadata,
    })
    assert node.url == "https://example.org/books/safe"


@pytest.mark.parametrize("malformed", (
    "PRIVATE_SECRET",
    [],
    {"friendly_url": "https://example.org/books/unexpected?token=PRIVATE"},
))
def test_malformed_nested_search_result_cannot_invent_link_or_abort(malformed):
    node = node_from_row({
        "book_id": "ed1", "title": "Same title",
        "metadata": {"search_result": malformed},
    })
    assert node.url == ""


def test_two_editions_with_same_title_keep_distinct_public_links():
    first = node_from_row({
        "book_id": "edition-a", "title": "Same title",
        "friendly_url": "https://example.org/books/a",
    })
    second = node_from_row({
        "book_id": "edition-b", "title": "Same title",
        "friendly_url": "https://example.org/books/b",
    })
    assert first.book.book_id == "edition-a"
    assert second.book.book_id == "edition-b"
    assert first.url != second.url
