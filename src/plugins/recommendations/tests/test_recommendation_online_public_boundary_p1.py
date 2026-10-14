"""#1599: the common detail must offer external links only for public notices.

Offline tests only: no browser navigation, provider call or secret-bearing result.
"""
import pytest

from recommendations import _external_book_url


class StringBomb:
    def __str__(self):
        raise AssertionError("untrusted URL must never be coerced")


@pytest.mark.parametrize("url", (
    "https://example.org/book/42",
    "http://catalogue.example.org/notice/123",
    "https://example.org:443/notice",
    "http://example.org:80/book",
    "https://xn--bcher-kva.example/books/1",
    "https://example.org/books/space%20name",
))
def test_attested_public_notice_remains_available(url):
    assert _external_book_url(url) == url


@pytest.mark.parametrize("url", (
    "https://example.org:8443/book/42",
    "http://example.org:8080/book",
    "https://example.org:444/acquisition",
    "https://127.1/book",
    "http://0177.0.0.1/notice",
    "https://reader.local/book",
    "https://example.org/download/book",
    "https://example.org/books/42/license",
    "https://example.org/token/PRIVATE_ACCESS",
    "https://example.org/authorize/session",
    "https://example.org/books/book.acsm",
    "https://example.org/books/book.epub",
    "https://example.org/books/book.lcpl",
    "https://example.org/%64ownload/book",
    "https://example.org/books%2flicense%2fsecret",
    "https://example.org/books%252fdownload%252fsecret",
    "https://example.org/books%25252fdownload%25252fsecret",
    "https://example.org/a%0dheader",
    "https://example.org/a%250dheader",
    "https://example.org/a%25250dheader",
    "https://example.org/book?sig=PRIVATE_SECRET",
    "https://user:PRIVATE@example.org/book",
    "https://example.org/book#PRIVATE_SESSION",
    "https://example.org/notice\x00token",
    "https://example.org/notice\x7ftoken",
    "https://example．org/book",
))
def test_private_or_ambiguous_links_never_reach_public_detail(url):
    assert _external_book_url(url) is None


@pytest.mark.parametrize("value", (
    None, False, 1, {}, ["https://example.org/book"], StringBomb(),
    "https://example.org/" + "x" * 2048,
))
def test_untrusted_url_types_and_lengths_fail_closed(value):
    assert _external_book_url(value) is None


def test_unknown_percent_sequences_do_not_turn_into_private_paths():
    assert _external_book_url("https://example.org/book%25value") == "https://example.org/book%25value"
