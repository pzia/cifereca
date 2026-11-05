"""P0 #3867: provider-qualified public notices, generic Core URL fence.

The supplier adapter checks the observed book-detail origin and path before
emitting its closed marker. Core rejects raw/unattested catalogue URLs,
private network endpoints, signed queries and redirects; it does not own the
supplier's domain list. No HTTP, delivery, files or permissions.
"""
from __future__ import annotations

import pytest

from cifereca.core.search_orchestration import _numilog_public_catalog_url


PROOF = "numilog.library_title_link"


@pytest.mark.parametrize("url", [
    "https://www.numilog.com/1234/valid-book.ebook",
    "https://www.numilog.com/999999999999/fiche_2024.ebook",
    "https://www.numilog.com/livres/catalog-book",
    "https://www.numilog.com/ebooks/archived-catalogue",
    "https://www.numilog.com/livres/catalog-book?ref=library",
    # A new qualified public book domain must not need a Core release.
    "https://books.new-publisher.org/public/title.ebook",
])
def test_exact_adapter_proof_retains_safe_public_https_without_domain_whitelist(url):
    assert _numilog_public_catalog_url(url, evidence=PROOF) == url
    assert _numilog_public_catalog_url(url) is None


@pytest.mark.parametrize("url", [
    "https://www.numilog.com/1234/valid-book.ebook?token=secret",
    "https://www.numilog.com/1234/valid-book.ebook?access_token=x",
    "https://www.numilog.com/1234/valid-book.ebook#fragment",
    "https://www.numilog.com/1234/..%2Fdelivery.ebook",
    "http://www.numilog.com/1234/valid-book.ebook",
    "https://user:pass@www.numilog.com/1234/valid-book.ebook",
    "https://www.numilog.com:8443/1234/valid-book.ebook",
    "https://www.numilog.com/1234/../valid-book.ebook",
    "https://www.numilog.com/1234/valid-book.ebook\\shadow",
    "https://127.0.0.1/1234/valid-book.ebook",
    "https://[::1]/1234/valid-book.ebook",
    "https://localhost/1234/valid-book.ebook",
    "https://service.internal/1234/valid-book.ebook",
    "https://metadata.google.internal/1234/valid-book.ebook",
    "https://invalid.example/1234/valid-book.ebook",
    "https://books.new-publisher.org/1234/valid-book.ebook\r\nHost: x",
])
def test_unsafe_or_signed_notice_rejected_even_with_source_marker(url):
    assert _numilog_public_catalog_url(url, evidence=PROOF) is None


@pytest.mark.parametrize("evidence", [
    None, "", "unknown", "numilog.library_title_link.different",
    True, 1, "legacy_receipt",
])
def test_unattested_or_weak_product_evidence_does_not_persist(evidence):
    assert _numilog_public_catalog_url(
        "https://www.numilog.com/1234/valid-book.ebook", evidence=evidence,
    ) is None


def test_query_free_public_covers_remain_generic_but_signed_covers_do_not():
    assert _numilog_public_catalog_url(
        "https://cdn.another-publisher.org/cover/abc.webp", cover=True,
    ) == "https://cdn.another-publisher.org/cover/abc.webp"
    for url in (
        "https://cdn.another-publisher.org/cover.jpg?signature=secret",
        "http://cdn.another-publisher.org/cover.jpg",
        "https://127.0.0.1/cover.jpg",
        "https://cdn.another-publisher.org/../private.jpg",
    ):
        assert _numilog_public_catalog_url(url, cover=True) is None
