"""#1346: cover images are public URLs, not credential-bearing acquisitions.

Synthetic catalogue/personal manifest rows only: no HTTP, GTK, or Kobo login.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca_kobo_catalog import KoboCatalogService


def _catalog():
    return KoboCatalogService(
        SimpleNamespace(base_url="https://vault.example/base"),
        credential_id="fixture",
    )


@pytest.mark.parametrize("unsafe", (
    "file:///home/PRIVATE_USER/cover.jpg",
    "javascript:PRIVATE_TOKEN",
    "https://user:PRIVATE_PASSWORD@cdn.example/cover.jpg",
    "https://cdn.example/cover.jpg?access_token=PRIVATE_SIGNED",
    "https://cdn.example/cover.jpg#PRIVATE_SESSION",
    "https://cdn.example/private%0aPRIVATE_BEARER.jpg",
    "https://cdn.example/cover%7F.jpg",
    "http://localhost/PRIVATE_COVER",
    "http://127.0.0.1/PRIVATE_COVER",
    "http://10.0.0.9/PRIVATE_COVER",
    "http://169.254.169.254/latest",
    "https://printer.lan/cover.jpg",
    "http://service.internal/cover.jpg",
    "https://cdn.example:8443/cover.jpg",
    "https://cdn.example/cover\\@evil.example",
    "https://cdn.example/cover.jpg\nPRIVATE_TOKEN",
    "https://cdn.example/cover.jpg\tPRIVATE_TOKEN",
    "https://[malformed/cover.jpg",
    "//127.0.0.1/cover.jpg",
    "/covers/book.jpg?token=PRIVATE_SIGNED",
))
def test_unsafe_cover_not_exported_as_thumbnail_or_raw_metadata(unsafe):
    catalog = _catalog()
    assert catalog.cover_url({"cover_url": unsafe}) is None
    for collection in ("catalog", "library"):
        item = catalog.normalize_item({
            "product_id": "edition-1",
            "title": "Safe title",
            "cover_url": unsafe,
            "collection": collection,
            "owned": collection == "library",
        })
        assert item.thumbnail_url is None
        assert "cover_url" not in item.metadata
        assert "catalog_cover" not in item.metadata
        public = json.dumps({
            "thumbnail": item.thumbnail_url,
            "metadata": item.metadata,
        }, ensure_ascii=False)
        assert "PRIVATE" not in public
        assert "/home/" not in public


@pytest.mark.parametrize("safe,resolved", (
    ("https://cdn.example/covers/a.jpg", "https://cdn.example/covers/a.jpg"),
    ("http://cdn.example/covers/a.jpg", "http://cdn.example/covers/a.jpg"),
    ("https://cdn.example:443/cover.jpg", "https://cdn.example:443/cover.jpg"),
    ("/covers/book.jpg", "https://vault.example/covers/book.jpg"),
    ("covers/book.jpg", "https://vault.example/base/covers/book.jpg"),
))
def test_public_images_and_relative_paths_preserve_expected_projection(safe, resolved):
    catalog = _catalog()
    row = catalog.normalize_item({
        "product_id": "edition-1", "title": "Book", "cover_url": safe,
        "collection": "catalog", "owned": False,
    })
    assert row.thumbnail_url == resolved
    assert row.metadata["cover_url"] == resolved
    assert row.metadata["catalog_cover"]["url"] == resolved


def test_nested_bad_variant_does_not_leak_when_public_fallback_exists():
    row = _catalog().normalize_item({
        "product_id": "edition-1", "title": "Book",
        "cover_url": "https://cdn.example/cover.jpg?token=PRIVATE_FIRST",
        "images": [
            {"thumbnail": "https://user:PRIVATE_PASSWORD@cdn.example/second.jpg"},
            {"medium": {"url": "https://cdn.example/safe.jpg"}},
            {"large": "https://cdn.example/third.jpg?token=PRIVATE_SECOND"},
        ],
    })
    assert row.thumbnail_url == "https://cdn.example/safe.jpg"
    assert row.metadata["cover_url"] == row.thumbnail_url
    assert row.metadata["catalog_cover"]["url"] == row.thumbnail_url
    assert "images" not in row.metadata
    assert "PRIVATE" not in json.dumps(row.metadata)


def test_nontext_cover_objects_not_rendered_or_stringified():
    class Dangerous:
        def __str__(self):
            raise AssertionError("PRIVATE_TOKEN must not be stringified")
    row = _catalog().normalize_item({
        "product_id": "edition-1", "title": "Book",
        "cover_url": Dangerous(), "images": [{"small": {"url": Dangerous()}}],
    })
    assert row.thumbnail_url is None
    assert "cover_url" not in row.metadata
    assert "images" not in row.metadata


def test_explicit_same_origin_local_vault_keeps_covers_without_trusting_other_hosts():
    catalog = KoboCatalogService(
        SimpleNamespace(base_url="http://127.0.0.1:9854/api"),
        credential_id="fixture",
    )
    expected = "http://127.0.0.1:9854/covers/book.jpg"
    assert catalog.cover_url({"cover_url": "/covers/book.jpg"}) == expected
    assert catalog.cover_url({"cover_url": expected}) == expected
    projected = catalog.normalize_item({
        "product_id": "edition-1", "title": "Book",
        "cover_url": expected, "collection": "catalog",
    })
    assert projected.thumbnail_url == expected
    assert projected.metadata["catalog_cover"]["url"] == expected
    for value in (
        "http://127.0.0.1:9855/covers/book.jpg",
        "http://127.0.0.2:9854/covers/book.jpg",
        "http://localhost:9854/covers/book.jpg",
        "http://127.0.0.1:9854/covers/book.jpg?token=PRIVATE",
        "http://127.0.0.1:9854/covers/book%0aPRIVATE.jpg",
    ):
        assert catalog.cover_url({"cover_url": value}) is None


@pytest.mark.parametrize("unsafe", (
    "https://cdn.example/cover%250APRIVATE_BEARER.jpg",
    "https://cdn.example/cover%25250dPRIVATE_SECRET.jpg",
    "https://cdn.example/cover%25252500PRIVATE_TOKEN.jpg",
    "https://cdn.example/cover%257FPRIVATE_TOKEN.jpg",
    "/covers/PRIVATE%250aCOVER.jpg",
    "covers/PRIVATE%25250dBOOK.jpg",
))
def test_nested_percent_encoded_controls_never_become_public_cover_urls(unsafe):
    catalog = _catalog()
    assert catalog.cover_url({"cover_url": unsafe}) is None
    projected = catalog.normalize_item({
        "product_id": "edition", "title": "Public",
        "cover_url": unsafe, "collection": "catalog",
    })
    assert projected.thumbnail_url is None
    assert "cover_url" not in projected.metadata
    assert "catalog_cover" not in projected.metadata
    public = json.dumps(projected.metadata, ensure_ascii=False)
    for private in ("PRIVATE_", "Bearer", "SECRET", "TOKEN"):
        assert private not in public


@pytest.mark.parametrize("cover", (
    "https://cdn.example/cover%20name.jpg",
    "https://cdn.example/cover%2520name.jpg",
    "https://cdn.example/cover%25name.jpg",
    "https://cdn.example/cover%GG.jpg",
    "/covers/book%20name.jpg",
))
def test_benign_percent_encoded_cover_paths_remain_valid(cover):
    catalog = _catalog()
    expected = catalog.cover_url({"cover_url": cover})
    assert expected is not None
    projected = catalog.normalize_item({"product_id": "edition", "cover_url": cover})
    assert projected.thumbnail_url == expected


@pytest.mark.parametrize("unsafe", (
    "http://127.1/PRIVATE_COVER.jpg",
    "http://127.0.1/PRIVATE_COVER.jpg",
    "http://0177.0.0.1/PRIVATE_COVER.jpg",
    "http://192.168.1/PRIVATE_COVER.jpg",
    "http://0x7f.0.0.1/PRIVATE_COVER.jpg",
    "https://cdn.example.123/PRIVATE_COVER.jpg",
))
def test_noncanonical_numeric_hosts_cannot_trigger_private_cover_fetch(unsafe):
    catalog = _catalog()
    assert catalog.cover_url({"cover_url": unsafe}) is None
    result = catalog.normalize_item({
        "product_id": "edition", "title": "Public",
        "cover_url": unsafe, "collection": "catalog",
    })
    assert result.thumbnail_url is None
    assert "cover_url" not in result.metadata
    assert "catalog_cover" not in result.metadata
    assert "PRIVATE" not in json.dumps(result.metadata)


def test_configured_local_vault_origin_still_allows_its_explicit_cover():
    catalog = KoboCatalogService(
        SimpleNamespace(base_url="http://127.0.0.1:9854/api"),
        credential_id="fixture",
    )
    assert catalog.cover_url({
        "cover_url": "http://127.0.0.1:9854/covers/cover.jpg",
    }) == "http://127.0.0.1:9854/covers/cover.jpg"
    assert catalog.cover_url({
        "cover_url": "http://127.1:9854/covers/private.jpg",
    }) is None
