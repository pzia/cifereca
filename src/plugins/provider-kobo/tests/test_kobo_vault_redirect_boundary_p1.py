"""#1346: Kobo Vault may not redirect requests containing private account data.

All HTTP exchanges are local MockTransport requests. A malicious 307/308
must never replay acquisition POST data or API tokens to another origin.
"""
from __future__ import annotations

import json

import httpx
import pytest

from cifereca_kobo_vault import KoboVaultClient, KoboVaultError


@pytest.mark.parametrize("status", (301, 302, 303, 307, 308))
@pytest.mark.parametrize("location", (
    "https://evil.example/PRIVATE_BOOK?access_token=PRIVATE_REDIRECT",
    "/delegated/PRIVATE_OTHER_ENDPOINT?token=PRIVATE_REDIRECT",
))
def test_acquisition_redirect_never_replays_private_post(status, location):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) != 1:
            pytest.fail("redirect must not issue any second request")
        assert request.url.path == "/api/v2/library/book-1/acquisition"
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer PRIVATE_API_TOKEN"
        assert json.loads(request.content) == {
            "mode": "delegated", "revision_id": "PRIVATE_REVISION",
        }
        return httpx.Response(
            status, headers={"Location": location},
            json={"error": {"code": "PRIVATE_CODE",
                            "message": "Bearer PRIVATE_RESPONSE"}},
        )

    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_API_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.client.follow_redirects is False
        with pytest.raises(KoboVaultError) as exc:
            client.acquisition("book-1", mode="delegated",
                               revision_id="PRIVATE_REVISION")
        assert exc.value.code == "redirect_denied"
        assert exc.value.status_code == status
        assert len(seen) == 1
        assert "PRIVATE" not in str(exc.value)
        assert "evil.example" not in str(exc.value)
        assert "Location" not in str(exc.value)
    finally:
        client.close()


@pytest.mark.parametrize("operation,expected_path,expected_method", (
    ("status", "/api/v2/status", "GET"),
    ("account", "/api/v2/account", "GET"),
    ("search_catalog", "/api/v2/catalog/search", "GET"),
    ("search_library", "/api/v2/library/search", "GET"),
    ("library_manifest_page", "/api/v2/library/manifest", "GET"),
    ("library_item", "/api/v2/library/book-1", "GET"),
))
def test_other_provider_routes_reject_redirects_without_second_get(
    operation, expected_path, expected_method,
):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.path == expected_path
        assert request.method == expected_method
        return httpx.Response(
            307,
            headers={"Location": "https://attacker.example/PRIVATE_ACCT"},
        )

    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    try:
        kwargs = {"product_id": "book-1"} if operation == "library_item" else {}
        if operation in {"search_catalog", "search_library"}:
            kwargs["query"] = "book"
        with pytest.raises(KoboVaultError) as error:
            getattr(client, operation)(**kwargs)
        assert error.value.code == "redirect_denied"
        assert error.value.status_code == 307
        assert len(seen) == 1
        assert "PRIVATE" not in str(error.value)
    finally:
        client.close()


@pytest.mark.parametrize("status", (300, 304, 399))
def test_any_non_authoritative_3xx_is_rejected(status):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, headers={"Location": "/next"})

    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(KoboVaultError) as error:
            client.status()
        assert error.value.status_code == status
        assert error.value.code == "redirect_denied"
        assert len(seen) == 1
    finally:
        client.close()


def test_internal_json_helper_cannot_opt_back_in_to_redirects():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            308, headers={"Location": "https://attacker.example/PRIVATE"},
        )

    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(KoboVaultError, match="redirection HTTP refusée"):
            client._json("GET", "/account", follow_redirects=True)
        assert len(seen) == 1
    finally:
        client.close()


def test_successful_api_payload_and_401_denial_keep_original_contract():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if len(seen) == 1:
            return httpx.Response(200, json={"api_version": "2.3"})
        return httpx.Response(401, json={"error": {
            "code": "PRIVATE", "message": "Bearer PRIVATE_SECRET",
        }})

    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.status()["api_version"] == "2.3"
        with pytest.raises(KoboVaultError) as error:
            client.account()
        assert error.value.code == "invalid_token"
        assert error.value.status_code == 401
        assert "PRIVATE" not in str(error.value)
        assert seen == ["/api/v2/status", "/api/v2/account"]
    finally:
        client.close()
