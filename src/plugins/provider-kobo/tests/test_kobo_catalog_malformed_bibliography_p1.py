"""#1346: provider bibliographic arrays must not stringify untrusted objects.

All input is synthetic. No real account, acquisition, network or local writes.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import cifereca_kobo_catalog as module
from cifereca_kobo_catalog import KoboCatalogService


class Bomb:
    def __str__(self):
        raise AssertionError("untrusted object was stringified")

    def __repr__(self):
        raise AssertionError("untrusted object was rendered")


def _catalog():
    return KoboCatalogService(
        SimpleNamespace(base_url="https://vault.example"),
        credential_id="synthetic",
    )


def test_malformed_authors_and_languages_are_removed_from_public_details(monkeypatch):
    def normalizer(value):
        if value == "PRIVATE_EXCEPTION":
            SecretError = type("Bearer_PRIVATE_ERROR_CLASS", (RuntimeError,), {})
            raise SecretError("/home/PRIVATE_ACCOUNT/books/secret.epub")
        if value == "PRIVATE_NONSTRING":
            return {"token": "PRIVATE_BAD_CANONICAL"}
        if value == "PRIVATE_INVALID":
            return None
        return {"FR": "fr", "EN": "en"}.get(value, value)

    monkeypatch.setattr(module, "normalize_language_tag", normalizer)
    bom = Bomb()
    item = {
        "product_id": "book-1", "title": "Safe title",
        "authors": [
            " Alice ", bom, {"token": "PRIVATE_AUTHOR_TOKEN"},
            False, "Bob", "", " ",
        ],
        "author": {"access_token": "PRIVATE_SINGLE_AUTHOR"},
        "language": "PRIVATE_EXCEPTION",
        "languages": [
            "FR", bom, {"token": "PRIVATE_LANGUAGE_TOKEN"}, False,
            "EN", "FR", "PRIVATE_INVALID", "PRIVATE_EXCEPTION",
            "PRIVATE_NONSTRING", " ",
        ],
    }
    normalized = _catalog().normalize_item(item)
    assert normalized.author == "Alice, Bob"
    assert normalized.metadata["authors"] == ["Alice", "Bob"]
    assert "author" not in normalized.metadata
    assert normalized.metadata["language"] is None
    assert normalized.metadata["languages"] == ["fr", "en", " "]
    assert normalized.owned is False and normalized.downloadable is None
    exported = json.dumps({
        "author": normalized.author, "authors": normalized.metadata["authors"],
        "language": normalized.metadata["language"],
        "languages": normalized.metadata["languages"],
    })
    assert "PRIVATE" not in exported and "Bearer" not in exported


@pytest.mark.parametrize("raw", [
    None, False, 17, {"access_token": "PRIVATE"},
    Bomb(),
])
def test_nontext_language_collection_is_not_exported_or_coerced(raw):
    item = {
        "product_id": "safe-edition", "title": "Safe title",
        "authors": raw, "author": raw, "language": raw,
        "languages": raw,
    }
    detail = _catalog().normalize_item(item)
    assert detail.author is None
    assert "authors" not in detail.metadata
    assert "author" not in detail.metadata
    assert "language" not in detail.metadata
    assert "languages" not in detail.metadata


def test_bad_bibliography_hit_does_not_abort_other_catalog_results():
    class Client:
        base_url = "https://vault.example"
        last_search_merge_rejections = ()

        def search(self, query, **_kwargs):
            return {"items": [
                {"product_id": "bad-bibliography", "title": "Unusual",
                 "authors": [{"token": "PRIVATE"}],
                 "languages": [False, {"token": "PRIVATE"}]},
                {"product_id": "good-bibliography", "title": "Normal",
                 "authors": ["Alice", "Bob"], "languages": ["fr", "en"]},
            ]}

    catalogue = KoboCatalogService(Client(), credential_id="synthetic")
    results = catalogue.search("book")
    assert [row.record_id for row in results] == [
        "bad-bibliography", "good-bibliography",
    ]
    assert results[0].author is None
    assert results[0].metadata["authors"] == []
    assert results[0].metadata["languages"] == []
    assert results[1].author == "Alice, Bob"
    assert results[1].metadata["languages"] == ["fr", "en"]
    assert catalogue.last_search_rejections == []


def test_legacy_scalar_author_and_normal_language_values_remain_compatible():
    normalized = _catalog().normalize_item({
        "product_id": "book-1", "title": "Title",
        "author": " Alice ", "language": "FR", "languages": ["FR", "en"],
    })
    assert normalized.author == "Alice"
    assert normalized.metadata["author"] == " Alice "
    assert normalized.metadata["language"] == "fr"
    assert normalized.metadata["languages"] == ["fr", "en"]
