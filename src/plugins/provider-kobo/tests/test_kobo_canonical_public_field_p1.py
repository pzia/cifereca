"""#1917: accepted general-search aliases must never become Vault field selectors.

Synthetic HTTP only: no Kobo account, personal network, acquisition or DRM.
"""
import httpx
import pytest

from cifereca_kobo_vault import KoboVaultClient
from tests.test_kobo_manifest_policy_p1 import session_with_vault


@pytest.mark.parametrize("field", ("all", "ALL", "All", " all "))
@pytest.mark.parametrize("scope,expected_routes", (
    ("catalog", ("/api/v2/catalog/search",)),
    ("all", ("/api/v2/catalog/search", "/api/v2/library/search")),
    ("digital", ("/api/v2/catalog/search", "/api/v2/library/search")),
))
def test_declared_all_field_is_canonical_on_every_vault_request(field, scope, expected_routes):
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        assert request.url.path in expected_routes
        return httpx.Response(200, json={"items": []})

    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    session = session_with_vault(client)
    try:
        assert session.search("Some book", field=field, scope=scope) == []
        assert tuple(request.url.path for request in requests) == expected_routes
        assert all(
            dict(request.url.params).get("field") == "all"
            for request in requests
        )
        assert session.last_search_warnings == []
    finally:
        session.close()
