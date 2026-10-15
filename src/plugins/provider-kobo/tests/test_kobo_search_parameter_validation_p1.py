"""#1346: Kobo Vault discovery inputs are validated before HTTP requests.

MockTransport only: no live account, personal library, acquisition or DRM.
"""
from __future__ import annotations

import json

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_kobo_vault import KoboVaultClient


def _client():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        if request.url.path == "/api/v2/catalog/search":
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/api/v2/library/search":
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/api/v2/library/manifest":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        pytest.fail("unexpected Kobo Vault request")

    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_API_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    return client, seen


@pytest.mark.parametrize("kwargs", [
    {"query": None},
    {"query": False},
    {"query": ["PRIVATE_QUERY"]},
    {"query": "book\nPRIVATE_ACCESS_TOKEN"},
    {"query": "x" * 4097},
    {"field": False},
    {"field": ["title"]},
    {"field": "title\rPRIVATE_ACCOUNT"},
    {"field": "x" * 129},
    {"field": ""},
    {"limit": None},
    {"limit": False},
    {"limit": 12.1},
    {"limit": {"PRIVATE": "TOKEN"}},
    {"limit": "Bearer PRIVATE_TOKEN"},
    {"limit": "9" * 17},
    {"page": None},
    {"page": True},
    {"page": 1.5},
    {"page": "PRIVATE_SIGNED_URL"},
    {"page": "1" * 17},
    {"language": False},
    {"language": ["en"]},
    {"language": "en\x00PRIVATE_TOKEN"},
])
def test_malformed_catalogue_controls_fail_before_any_network(kwargs):
    client, seen = _client()
    try:
        args = {"query": "example"}
        args.update(kwargs)
        with pytest.raises(ProviderError) as exc:
            client.search_catalog(**args)
        assert seen == []
        for secret in ("PRIVATE", "Bearer", "example", "SIGNED_URL"):
            assert secret not in str(exc.value)
    finally:
        client.close()


@pytest.mark.parametrize("kwargs", [
    {"query": ["PRIVATE_QUERY"]},
    {"query": "x\rPRIVATE_TOKEN"},
    {"field": 17},
    {"field": ""},
    {"limit": True},
    {"limit": "PRIVATE_TOKEN"},
    {"cursor": False},
    {"cursor": 123},
    {"cursor": ["PRIVATE_CURSOR"]},
    {"cursor": "opaque\nPRIVATE_TOKEN"},
    {"cursor": "a" * 4097},
])
def test_malformed_personal_controls_fail_without_private_get(kwargs):
    client, seen = _client()
    try:
        args = {"query": "example"}
        args.update(kwargs)
        with pytest.raises(ProviderError) as exc:
            client.search_library(**args)
        assert seen == []
        assert "PRIVATE" not in str(exc.value)
    finally:
        client.close()


@pytest.mark.parametrize("kwargs", [
    {"limit": False},
    {"limit": "Bearer PRIVATE_TOKEN"},
    {"limit": "9" * 17},
    {"cursor": True},
    {"cursor": {"PRIVATE": "TOKEN"}},
    {"cursor": "bad\rPRIVATE_CURSOR"},
])
def test_manifest_pagination_rejects_bad_control_before_http(kwargs):
    client, seen = _client()
    try:
        with pytest.raises(ProviderError) as exc:
            client.library_manifest_page(**kwargs)
        assert seen == []
        assert "PRIVATE" not in str(exc.value)
    finally:
        client.close()


@pytest.mark.parametrize("scope", ["all", "library"])
def test_bad_personal_cursor_blocks_entire_search_before_public_get(scope):
    client, seen = _client()
    try:
        with pytest.raises(ProviderError, match="paramètre de recherche invalide"):
            client.search("example", scope=scope, cursor="PRIVATE\nTOKEN")
        assert seen == []
    finally:
        client.close()


def test_valid_unicode_and_legacy_numeric_controls_preserve_search_behavior():
    client, seen = _client()
    try:
        client.search_catalog(
            "été du livre", field="title", limit="120", page="-1",
            language="fr-CA",
        )
        client.search_library(
            "livre café", field="author", limit="0",
            cursor="opaque/+cursor==",
        )
        client.library_manifest_page(cursor="opaque/+next==", limit="999")
        assert [request.url.path for request in seen] == [
            "/api/v2/catalog/search", "/api/v2/library/search",
            "/api/v2/library/manifest",
        ]
        p0, p1, p2 = [dict(request.url.params) for request in seen]
        assert p0 == {
            "q": "été du livre", "field": "title", "limit": "100",
            "page": "0", "language": "fr-CA",
        }
        assert p1 == {
            "q": "livre café", "field": "author", "limit": "1",
            "cursor": "opaque/+cursor==",
        }
        assert p2 == {"limit": "500", "cursor": "opaque/+next=="}
    finally:
        client.close()


def test_empty_query_and_omitted_cursor_remain_supported():
    client, seen = _client()
    try:
        assert client.search("", scope="all")["items"] == []
        assert len(seen) == 2
        assert all("cursor" not in request.url.params for request in seen)
        seen.clear()
        client.search_catalog("", language="")
        assert len(seen) == 1
        assert "language" not in seen[0].url.params
    finally:
        client.close()