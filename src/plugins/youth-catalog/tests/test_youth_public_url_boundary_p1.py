"""#1599: only ordinary public notices and covers may reach the shared detail.

Offline URL tests. No browser navigation, provider, file access or GTK.
"""
import pytest

from youth_page import public_url


@pytest.mark.parametrize("url", (
    "https://catalogue.example.org/book/123",
    "http://example.org:80/notice/1",
    "https://example.org:443/notice/1",
    "https://covers.openlibrary.org/b/id/123-M.jpg?default=false",
    "https://example.org/notice?titre=livre&lang=fr",
    "https://example.org/images/nom%20livre.jpg",
    "https://xn--bcher-kva.example/buch/2",
))
def test_regular_public_notice_and_cover_links_remain_available(url):
    assert public_url(url) == url


@pytest.mark.parametrize("url", (
    "https://127.1/book",
    "http://0177.0.0.1/book",
    "https://example．org/book",
    "https://reader.home.arpa/book",
    "https://example.org/download/ebook",
    "https://example.org/books%2Fdownload%2Febook",
    "https://example.org/books%252Flicense%252Febook",
    "https://example.org/books%25252Facquisition%25252Febook",
    "https://example.org/books/title.acsm",
    "https://example.org/books/title.lcpl",
    "https://example.org/books/title.epub",
    "https://example.org/token/private",
    "https://example.org/book?sig=PRIVATE_SIGNATURE",
    "https://example.org/book?%73ig=PRIVATE_SIGNATURE",
    "https://example.org/book?%2573ig=PRIVATE_SIGNATURE",
    "https://example.org/book?key=PRIVATE_ACCESS_KEY",
    "https://example.org/book?X-Amz-Signature=PRIVATE_SIGNATURE",
    "https://example.org/book?auth=PRIVATE",
    "https://example.org/book?title=%0DPRIVATE",
    "https://example.org/images/photo%0Ahidden.jpg",
    "https://example.org/images/photo%250Ahidden.jpg",
    "https://example.org/images/photo%25250Ahidden.jpg",
    "https://example.org/book#PRIVATE_SESSION",
    "https://example.org:8443/notice",
    "https://user:PRIVATE@example.org/book",
))
def test_signed_private_or_acquisition_links_are_never_displayed(url):
    assert public_url(url) == ""


def test_safe_secondary_notice_used_when_primary_url_is_private():
    from youth_page import detail_presentation
    from youth_catalog import YouthBook
    book = YouthBook(
        source_id="sample",
        record_id="1",
        title="Example",
        friendly_url="https://example.org/acquisition/license.acsm",
        notice_url="https://example.org/notice/1",
        cover_url="https://example.org/download/epub",
    )
    detail = detail_presentation(book)
    assert len(detail.links) == 1
    assert detail.links[0].url == "https://example.org/notice/1"
    assert detail.thumbnail_url is None
