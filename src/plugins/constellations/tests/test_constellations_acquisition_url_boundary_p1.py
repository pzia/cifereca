"""#1599: Constellations notice links are not acquisition or local URLs."""
import pytest

from constellation_view_model import node_from_row, public_url


@pytest.mark.parametrize("url", (
    "https://127.1/books/123",
    "http://0177.0.0.1/books/123",
    "https://2130706433/books/123",
    "https://example．org/books/123",
    "https://example.org/download/title",
    "https://example.org/books%2Fdownload%2Ftitle",
    "https://example.org/books%252Flicense%252Ftitle",
    "https://example.org/books%25252Facquisition%25252Ftitle",
    "https://example.org/fulfillment/edition",
    "https://example.org/loan/abc",
    "https://example.org/drm/rights",
    "https://example.org/book.acsm",
    "https://example.org/book.lcpl",
    "https://example.org/book.epub",
    "https://example.org/book.kepub",
    "https://example.org/book.pdf",
    "https://example.org/books%2Fbook%2Eacsm",
    "https://example.org/images%250Aprivate.jpg",
    "https://example.org/images%25250Dprivate.jpg",
    "https://example.org/books/title%00hidden",
))
def test_private_encoded_or_delivery_links_are_rejected(url):
    assert public_url(url) == ""
    assert node_from_row({"book_id": "a", "title": "Book", "friendly_url": url}).url == ""


@pytest.mark.parametrize("url", (
    "https://example.org/books/123",
    "http://example.org/catalogue/edition",
    "https://example.org:443/books/123",
    "https://example.org/books/une%20histoire",
    "https://example.org/books/%25offre",
    "https://xn--bcher-kva.example/books/123",
))
def test_safe_public_notice_urls_are_preserved(url):
    assert public_url(url) == url


def test_disguised_primary_delivery_link_does_not_hide_safe_secondary_notice():
    node = node_from_row({
        "book_id": "a", "title": "Public title",
        "friendly_url": "https://example.org/books%252Facquisition%252Fprivate.acsm",
        "metadata": {
            "search_result": {"friendly_url": "https://catalogue.example.org/books/public-123"},
        },
    })
    assert node.url == "https://catalogue.example.org/books/public-123"
    assert node.book.title == "Public title"
