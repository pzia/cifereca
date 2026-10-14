"""#1346: Kobo product URLs are public bibliographic links, never signed rights.

Exercised with synthetic catalogue payloads, no live account or HTTP.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca_kobo_catalog import KoboCatalogService


def _catalog(client=None):
    return KoboCatalogService(
        client or SimpleNamespace(base_url="https://vault.example"),
        credential_id="fixture",
    )


@pytest.mark.parametrize("invalid", [
    "file:///home/PRIVATE_USER/book.epub",
    "javascript:PRIVATE_TOKEN",
    "data:text/html,PRIVATE_TOKEN",
    "https://user:PRIVATE_PASSWORD@www.kobo.com/fr/ebook/book",
    "https://www.kobo.com/fr/ebook/book?token=PRIVATE_SIGNED",
    "https://www.kobo.com/fr/ebook/book#PRIVATE_LICENSE",
    "https://www.kobo.com/fr/ebook/book\nPRIVATE_TOKEN",
    "https://www.kobo.com/fr/ebook/book\rPRIVATE_TOKEN",
    "https://www.kobo.com/fr/ebook/book\tPRIVATE_TOKEN",
    "https://www.kobo.com\\@evil.example/fr/ebook/book",
    "https://www.kobo.com/%0Abook",
    "https://www.kobo.com/%7fPRIVATE_TOKEN",
    "https://127.0.0.1:443/fr/ebook/book",
    "https://192.168.1.1/fr/ebook/book",
    "http://169.254.169.254/latest/meta-data",
    "http://localhost/fr/ebook/book",
    "https://printer.lan/fr/ebook/book",
    "https://internal.local/fr/ebook/book",
    "https://www.kobo.com:8443/fr/ebook/book",
    "https://www.kobo.com/download/PRIVATE_TOKEN",
    "https://www.kobo.com/acquisition/book",
    "https://www.kobo.com/license/PRIVATE_LICENSE",
    "https://[broken/path",
    "https://www.kobo.com/" + "x" * 2050,
    "https://www.kobo.com/fr/ebook/book ",
    "https://www.kobo.com/fr/ebook/PRIVATE_BOOK?auth=PRIVATE_SECRET",
    "PRIVATE_OPAQUE_ID",
    None, False, ["https://www.kobo.com"], {"url": "PRIVATE_TOKEN"}, 17,
])
@pytest.mark.parametrize("collection,owned", [
    ("catalog", False), ("library", True),
])
def test_invalid_product_urls_never_reach_public_links_or_metadata(
    invalid, collection, owned,
):
    projected = _catalog().normalize_item({
        "product_id": "public-edition",
        "title": "Livre attesté",
        "product_url": invalid,
        "owned": owned,
        "collection": collection,
    }, default_collection=collection)
    assert projected.record_id == "public-edition"
    assert projected.owned is owned
    assert projected.friendly_url is None
    assert "product_url" not in projected.metadata
    public = json.dumps({
        "friendly_url": projected.friendly_url,
        "metadata": projected.metadata,
    }, ensure_ascii=False)
    assert "PRIVATE" not in public
    assert "/home/" not in public


@pytest.mark.parametrize("public_url", [
    "https://www.kobo.com/fr/fr/ebook/un-livre",
    "https://www.kobo.com/en/ebook/a-book",
    "http://www.kobo.com/en/ebook/a-book",
    "https://www.kobo.com:443/en/ebook/a-book",
    "https://example.org/catalogue/book-123",
])
def test_public_book_page_is_preserved_in_links_and_metadata(public_url):
    projected = _catalog().normalize_item({
        "product_id": "edition-1",
        "title": "Public Book",
        "product_url": public_url,
        "owned": False,
    })
    assert projected.friendly_url == public_url
    assert projected.metadata["product_url"] == public_url
    assert projected.owned is False
    assert projected.downloadable is None


def test_one_unsafe_book_url_does_not_abort_other_search_results():
    class Client:
        base_url = "https://vault.example"
        last_search_merge_rejections = ()

        def search(self, query, **_kwargs):
            return {"items": [
                {"product_id": "signed", "title": "No public link",
                 "product_url": "https://www.kobo.com/ebook/book?token=PRIVATE_LICENSE"},
                {"product_id": "public", "title": "Public",
                 "product_url": "https://www.kobo.com/fr/ebook/book"},
            ]}
    results = _catalog(Client()).search("query", scope="catalog")
    assert len(results) == 2
    assert results[0].friendly_url is None
    assert "product_url" not in results[0].metadata
    assert results[1].friendly_url == "https://www.kobo.com/fr/ebook/book"
    assert results[1].metadata["product_url"] == results[1].friendly_url
    assert all(row.owned is False for row in results)


def test_untrusted_product_url_object_is_not_stringified():
    class NeverStringify:
        def __str__(self):
            raise AssertionError("PRIVATE_TOKEN __str__ must not be called")

        def __repr__(self):
            raise AssertionError("PRIVATE_TOKEN __repr__ must not be called")

    projected = _catalog().normalize_item({
        "product_id": "edition-1", "title": "Book",
        "product_url": NeverStringify(),
    })
    assert projected.friendly_url is None
    assert "product_url" not in projected.metadata
