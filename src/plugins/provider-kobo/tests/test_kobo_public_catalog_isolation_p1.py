"""#1346: Kobo public catalogue cannot attest personal ownership or acquisition."""
from __future__ import annotations

import json

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_kobo_catalog import KoboCatalogService
from cifereca_kobo_vault import KoboVaultClient


_POISONED_CATALOG = {
    "product_id": "public-edition",
    "title": "Public title",
    "description": "<p>Public synopsis</p>",
    "cover_url": "https://cdn.example/image.jpg",
    "price": {"amount": "2.99", "currency": "EUR"},
    "collection": "library",
    "owned": True, "downloadable": True, "archived": True,
    "account_id": "PRIVATE_OTHER_ACCOUNT",
    "credential_id": "PRIVATE_OTHER_CREDENTIAL",
    "source_credential": "PRIVATE_OTHER_SOURCE",
    "revision_id": "PRIVATE_OTHER_REV",
    "acquisition": {"url": "https://PRIVATE/?token=PRIVATE_TOKEN"},
    "entitlements": [{"owner": "PRIVATE_OWNER"}],
    "rights": {"owned": True}, "local_path": "/home/PRIVATE/test.epub",
    "download_url": "https://PRIVATE/download",
    "license_url": "https://PRIVATE/license",
    "signed_url": "https://PRIVATE/epub",
}


def _client():
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path == "/api/v2/catalog/search":
            return httpx.Response(200, json={"items": [_POISONED_CATALOG]})
        if request.url.path == "/api/v2/library/search":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        pytest.fail("unexpected request")

    return (KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    ), requests)


@pytest.mark.parametrize("scope,expected_requests", [
    ("catalog", ["/api/v2/catalog/search"]),
    ("CATALOG", ["/api/v2/catalog/search"]),
    ("all", ["/api/v2/catalog/search", "/api/v2/library/search"]),
])
def test_public_catalogue_cannot_forge_owned_book_or_download_right(
    scope, expected_requests,
):
    client, requests = _client()
    try:
        result = client.search("a novel", scope=scope)
        assert requests == expected_requests
        record = result["items"][0]
        assert record["product_id"] == "public-edition"
        assert record["title"] == "Public title"
        assert record["price"] == {"amount": "2.99", "currency": "EUR"}
        assert record["description"] == "<p>Public synopsis</p>"
        assert record["collection"] == "catalog"
        for name in (
            "owned", "downloadable", "archived", "account_id",
            "credential_id", "source_credential", "revision_id",
            "acquisition", "entitlements", "rights", "local_path",
            "download_url", "license_url", "signed_url",
        ):
            assert name not in record
        item = KoboCatalogService(client, credential_id="account").normalize_item(record)
        assert item.owned is False
        assert item.downloadable is None
        assert item.source_collection == "catalog"
        assert item.revision_id is None
        assert item.price == "2.99"
        public = json.dumps(record) + json.dumps(item.metadata)
        assert "PRIVATE" not in public
        if scope == "all":
            assert result["catalog"]["items"][0]["collection"] == "catalog"
            assert "owned" not in result["catalog"]["items"][0]
    finally:
        client.close()


@pytest.mark.parametrize("scope", [
    "", "catalogue", "catalog,library", "PRIVATE_SIGNED_SCOPE",
    3, None, {}, ["catalog"],
])
def test_unknown_search_scope_does_not_trigger_either_endpoint(scope):
    client, requests = _client()
    try:
        with pytest.raises(ProviderError) as exc:
            client.search("x", scope=scope)
        assert requests == []
        assert "PRIVATE" not in str(exc.value)
        assert client.last_search_merge_rejections == []
    finally:
        client.close()


def test_account_only_search_preserves_attested_rights_without_catalog_calls():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/api/v2/library/search":
            return httpx.Response(200, json={"items": [{
                "product_id": "owned-edition", "owned": True,
                "downloadable": False, "revision_id": "personal-rev",
                "collection": "library",
            }]})
        pytest.fail("catalogue should not be fetched for library-only scope")

    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    try:
        record = client.search("x", scope="library")["items"][0]
        assert record["owned"] is True
        assert record["downloadable"] is False
        assert record["revision_id"] == "personal-rev"
        assert seen == ["/api/v2/library/search"]
    finally:
        client.close()


def test_catalogue_poisoning_cannot_overwrite_explicit_personal_denial():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/api/v2/catalog/search":
            return httpx.Response(200, json={"items": [{
                **_POISONED_CATALOG, "product_id": "shared-edition",
            }]})
        if request.url.path == "/api/v2/library/search":
            return httpx.Response(200, json={"items": [{
                "product_id": "shared-edition", "owned": False,
                "downloadable": False, "revision_id": "personal-rev",
            }]})
        pytest.fail("unexpected endpoint")

    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    try:
        response = client.search("x", scope="all")
        record = response["items"][0]
        assert record["owned"] is False
        assert record["downloadable"] is False
        assert record["revision_id"] == "personal-rev"
        assert record["description"] == "<p>Public synopsis</p>"
        assert "PRIVATE" not in json.dumps(record)
        assert seen == ["/api/v2/catalog/search", "/api/v2/library/search"]
        item = KoboCatalogService(client, credential_id="account").normalize_item(record)
        assert item.owned is False and item.downloadable is False
    finally:
        client.close()
