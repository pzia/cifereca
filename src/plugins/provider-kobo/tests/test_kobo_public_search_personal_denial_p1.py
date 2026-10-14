"""#1346: a refused personal Kobo search cannot block public catalogue discovery.

MockTransport only, no real Kobo account, downloading, acquisition or secrets.
"""
from __future__ import annotations

import json

import httpx
import pytest

from cifereca_kobo_catalog import KoboCatalogService
from cifereca_kobo_vault import KoboVaultClient, KoboVaultError


PUBLIC = {
    "product_id": "edition-1",
    "title": "Public bibliography",
    "description": "Public summary",
    "owned": True, "downloadable": True,
    "collection": "library",
    "account_id": "PRIVATE_FOREIGN_ACCOUNT",
    "revision_id": "PRIVATE_REVISION",
    "rights": {"owner": "PRIVATE_OWNER"},
    "acquisition": {"url": "https://example.test/PRIVATE_SIGNED"},
    "download_url": "https://example.test/PRIVATE_TOKEN",
}


@pytest.mark.parametrize("status,code", [
    (401, "invalid_token"),
    (403, "insufficient_scope"),
    (403, "forbidden"),
])
def test_personal_denial_falls_back_to_public_search_without_rights(status, code):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/api/v2/catalog/search":
            assert request.url.params["q"] == "test"
            assert request.url.params["field"] == "title"
            return httpx.Response(200, json={"items": [PUBLIC]})
        if request.url.path == "/api/v2/library/search":
            return httpx.Response(status, json={"error": {
                "code": code, "message": "Bearer PRIVATE_TOKEN at PRIVATE_PATH",
            }})
        pytest.fail(f"Unexpected route: {request.url.path}")

    vault = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handle),
    )
    service = KoboCatalogService(vault, credential_id="fixture")
    try:
        items = service.search("test", field="title")
        assert paths == [
            "/api/v2/catalog/search", "/api/v2/library/search",
            "/api/v2/catalog/search",
        ]
        assert len(items) == 1
        item = items[0]
        assert item.record_id == "edition-1"
        assert item.title == "Public bibliography"
        assert item.description == "Public summary"
        assert item.source_collection == "catalog"
        assert item.owned is False
        assert item.downloadable is None
        assert item.revision_id is None
        assert item.download_url is None
        assert service.last_search_rejections == [{
            "source": "library",
            "type": "invalid_scope",
            "reason": "personal_search_denied",
        }]
        exported = json.dumps({
            "metadata": item.metadata,
            "rejections": service.last_search_rejections,
        })
        for secret in (
            "PRIVATE", "Bearer", "account_id", "revision_id",
            "download_url", "acquisition", "rights",
        ):
            assert secret not in exported
    finally:
        vault.close()


@pytest.mark.parametrize("status", [400, 404, 409, 429, 500, 503])
def test_unrelated_http_errors_never_fall_back_to_catalogue(status):
    paths = []

    def handle(request):
        paths.append(request.url.path)
        if request.url.path == "/api/v2/catalog/search":
            return httpx.Response(200, json={"items": [PUBLIC]})
        if request.url.path == "/api/v2/library/search":
            return httpx.Response(status, json={"error": {
                "code": "PRIVATE_PROVIDER_CODE", "message": "PRIVATE_MESSAGE",
            }})
        pytest.fail(f"Unexpected route: {request.url.path}")

    vault = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handle),
    )
    service = KoboCatalogService(vault, credential_id="fixture")
    try:
        with pytest.raises(KoboVaultError) as err:
            service.search("test")
        assert err.value.status_code == status
        assert paths == ["/api/v2/catalog/search", "/api/v2/library/search"]
        assert service.last_search_rejections == []
        assert "PRIVATE" not in str(err.value)
    finally:
        vault.close()


def test_personal_denial_then_recovery_resets_fallback_diagnostics():
    state = {"denied": True}

    def handle(request):
        if request.url.path == "/api/v2/catalog/search":
            return httpx.Response(200, json={"items": [PUBLIC]})
        if request.url.path == "/api/v2/library/search":
            if state["denied"]:
                return httpx.Response(403, json={"error": {"code": "forbidden"}})
            return httpx.Response(200, json={"items": [{
                "product_id": "edition-1", "title": "Personal bibliography",
                "collection": "library", "owned": True,
                "account_id": "alice", "revision_id": "r1",
            }]})
        pytest.fail(f"Unexpected route: {request.url.path}")

    vault = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handle),
    )
    service = KoboCatalogService(vault, credential_id="fixture")
    try:
        first = service.search("test")
        assert first[0].owned is False
        assert service.last_search_rejections[0]["reason"] == "personal_search_denied"
        state["denied"] = False
        second = service.search("test")
        assert len(second) == 1
        assert second[0].owned is True
        assert second[0].revision_id == "r1"
        assert service.last_search_rejections == []
    finally:
        vault.close()


def test_fallback_catalog_failure_is_not_silently_converted_to_empty_success():
    calls = {"catalog": 0}

    def handle(request):
        if request.url.path == "/api/v2/catalog/search":
            calls["catalog"] += 1
            if calls["catalog"] == 2:
                return httpx.Response(503, json={"error": {"message": "PRIVATE_SERVER"}})
            return httpx.Response(200, json={"items": [PUBLIC]})
        if request.url.path == "/api/v2/library/search":
            return httpx.Response(403, json={"error": {"code": "forbidden"}})
        pytest.fail(f"Unexpected route: {request.url.path}")

    vault = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handle),
    )
    service = KoboCatalogService(vault, credential_id="fixture")
    try:
        with pytest.raises(KoboVaultError) as error:
            service.search("test")
        assert error.value.status_code == 503
        assert "PRIVATE" not in str(error.value)
        assert calls["catalog"] == 2
    finally:
        vault.close()
