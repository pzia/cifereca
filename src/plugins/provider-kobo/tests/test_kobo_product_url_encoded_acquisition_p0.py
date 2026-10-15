"""#1346: public Kobo product pages cannot hide acquisition routes or private hosts.

Synthetic bibliographic records only; no HTTP, account, downloads or DRM.
"""
from __future__ import annotations

import json

import pytest

from cifereca_kobo_catalog import KoboCatalogService
from tests.test_kobo_public_product_url_p1 import _catalog


@pytest.mark.parametrize("url", [
    "https://www.kobo.com/%64ownload/private",
    "https://www.kobo.com/books%2flicense%2fPRIVATE_TOKEN",
    "https://www.kobo.com/books%252fdownload%252fPRIVATE_TOKEN",
    "https://www.kobo.com/books%25252facquisition%25252fPRIVATE_TOKEN",
    "https://www.kobo.com/books%2525252ftoken%2525252fPRIVATE_TOKEN",
    "https://www.kobo.com/books%2F%6Fauth/private",
    "https://www.kobo.com/%6C%69%63%65%6E%73%65/private",
    "https://www.kobo.com/fr/books/PRIVATE_BOOK%2eacsm",
    "https://www.kobo.com/fr/books/PRIVATE_BOOK%252eLCPL",
    "https://www.kobo.com/fr/books/PRIVATE_BOOK.epub",
    "https://www.kobo.com/fr/books/PRIVATE_BOOK.kepub",
    "https://www.kobo.com/books%0Aprivate",
    "https://www.kobo.com/books%250D%250APRIVATE_TOKEN",
    "https://127.1/fr/ebook/PRIVATE_BOOK",
    "http://0177.0.0.1/fr/ebook/PRIVATE_BOOK",
    "https://example．org/fr/ebook/PRIVATE_BOOK",
    "https://reader.123/fr/ebook/PRIVATE_BOOK",
    "https://www.kobo.com/%252525252fdownload/PRIVATE_BOOK",
])
@pytest.mark.parametrize("ownership", (False, True))
def test_encoded_private_product_url_never_crosses_search_result_boundary(url, ownership):
    projected = _catalog().normalize_item({
        "product_id": "synthetic-edition",
        "title": "Safe public title",
        "product_url": url,
        "owned": ownership,
        "collection": "library" if ownership else "catalog",
    }, default_collection="library" if ownership else "catalog")
    assert projected.owned is ownership
    assert projected.friendly_url is None
    assert "product_url" not in projected.metadata
    exported = json.dumps({
        "friendly_url": projected.friendly_url,
        "metadata": projected.metadata,
    }, ensure_ascii=False)
    assert "PRIVATE" not in exported
    assert "Bearer" not in exported


@pytest.mark.parametrize("url", [
    "https://www.kobo.com/fr/ebook/un-livre",
    "https://www.kobo.com/fr/ebook/un%20livre",
    "https://www.kobo.com:443/en/ebook/a-book",
    "http://www.kobo.com/en/ebook/a-book",
    "https://www.kobo.com/fr/ebook/book%25name",
])
def test_ordinary_public_product_urls_remain_attested(url):
    projected = _catalog().normalize_item({
        "product_id": "edition-1",
        "title": "Safe",
        "product_url": url,
        "owned": False,
    })
    assert projected.friendly_url == url
    assert projected.metadata["product_url"] == url
    assert not projected.owned
    assert projected.downloadable is None


def test_private_catalogue_url_cannot_be_revived_as_metadata_in_search():
    class SyntheticVault:
        base_url = "https://vault.example"
        last_search_merge_rejections = ()

        def search(self, query, **options):
            return {"items": [
                {"product_id": "unsafe", "title": "Unsafe",
                 "product_url": "https://www.kobo.com/%25252fdownload/PRIVATE_BOOK"},
                {"product_id": "safe", "title": "Safe",
                 "product_url": "https://www.kobo.com/fr/ebook/safe"},
            ]}

    rows = KoboCatalogService(SyntheticVault(), credential_id="test").search(
        "safe", scope="catalog",
    )
    assert [row.record_id for row in rows] == ["unsafe", "safe"]
    assert rows[0].friendly_url is None
    assert "product_url" not in rows[0].metadata
    assert rows[1].friendly_url == "https://www.kobo.com/fr/ebook/safe"
    assert rows[1].metadata["product_url"] == rows[1].friendly_url
    assert all(row.downloadable is None and row.owned is False for row in rows)
