"""P0 #2797: retain only attested public Numilog catalogue links.

No provider fetch, acquisition or mutable rights, pure projection validator.
"""
from __future__ import annotations

import pytest

from cifereca.core.search_orchestration import _numilog_public_catalog_url


@pytest.mark.parametrize("url", [
    "https://www.numilog.com/1234/valid-book.ebook",
    "https://www.numilog.com/999999999999/fiche_2024.ebook",
    "https://www.numilog.com/1234/title.ebook?ref=library",
])
def test_canonical_public_ebook_notice_is_retained(url):
    assert _numilog_public_catalog_url(url) == url


@pytest.mark.parametrize("url", [
    "https://www.numilog.com/1234/valid-book.ebook?token=secret",
    "https://www.numilog.com/1234/valid-book.ebook#fragment",
    "https://www.numilog.com/1234/valid-book.pdf",
    "https://www.numilog.com/1234/..%2Fdelivery.ebook?access_token=x",
    "https://attacker.example/1234/valid-book.ebook",
    "http://www.numilog.com/1234/valid-book.ebook",
    "https://user:pass@www.numilog.com/1234/valid-book.ebook",
    "https://www.numilog.com:8443/1234/valid-book.ebook",
    "https://www.numilog.com/1234/../valid-book.ebook",
    "https://www.numilog.com/1234/valid-book.ebook\\shadow",
])
def test_delivery_or_unsafe_notice_never_enters_public_catalog(url):
    assert _numilog_public_catalog_url(url) is None


def test_legacy_public_book_path_keeps_compatibility_without_delivery_query():
    assert _numilog_public_catalog_url(
        "https://www.numilog.com/livres/catalog-book"
    ) == "https://www.numilog.com/livres/catalog-book"
    assert _numilog_public_catalog_url(
        "https://www.numilog.com/livres/catalog-book?token=opaque"
    ) is None
