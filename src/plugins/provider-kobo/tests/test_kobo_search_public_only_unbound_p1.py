"""#1346: account authority is verified *before* Kobo private search HTTP.

MockTransport fixtures exercise real Vault + catalogue + session projections.
No personal service, account credentials, downloads or DRM is contacted.
"""
from __future__ import annotations

import json

import httpx
import pytest

from cifereca.plugin_api import Account, ProviderError
from cifereca_kobo_catalog import KoboCatalogService
from cifereca_kobo_vault import KoboVaultClient, KoboVaultError
from tests.test_kobo_manifest_policy_p1 import session_with_vault


PUBLIC = {
    "product_id": "public-edition", "title": "Public bibliography",
    "owned": True, "downloadable": True,
    "collection": "library", "account_id": "PRIVATE_ACCOUNT",
    "revision_id": "PRIVATE_REVISION", "download_url": "PRIVATE_SIGNED",
}


def _case(*, library_status=503, catalog_status=200):
    paths = []

    def handle(request: httpx.Request):
        paths.append(request.url.path)
        if request.url.path == "/api/v2/catalog/search":
            if catalog_status != 200:
                return httpx.Response(catalog_status, json={"error": {
                    "code": "forbidden",
                    "message": "PRIVATE_CATALOGUE_MESSAGE",
                }})
            return httpx.Response(200, json={"items": [PUBLIC]})
        if request.url.path == "/api/v2/library/search":
            if library_status != 200:
                return httpx.Response(library_status, json={"error": {
                    "code": "forbidden", "message": "PRIVATE_PERSONAL_MESSAGE",
                }})
            return httpx.Response(200, json={"items": [{
                "product_id": "owned-edition", "title": "My purchased book",
                "owned": True, "downloadable": True, "account_id": "alice",
                "revision_id": "revision-1", "collection": "library",
            }]})
        pytest.fail("unexpected Kobo operation")

    vault = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handle),
    )
    session = session_with_vault(vault)
    return session, paths


@pytest.mark.parametrize("binding", [
    "disconnected", "empty", "different-provider", "different-credential",
    "two-accounts", "invalid-id",
])
@pytest.mark.parametrize("denial", [403, 429, 503])
def test_unbound_search_never_sends_private_get_even_if_personal_route_fails(
    binding, denial,
):
    session, paths = _case(library_status=denial)
    try:
        original = session._accounts[0]
        if binding == "disconnected":
            session.connected = False
        elif binding == "empty":
            session._accounts = []
        elif binding == "different-provider":
            session._accounts = [Account(
                unique_id="alice", display_name="Synthetic",
                provider_id="OTHER", credential_id=session.credential_id,
            )]
        elif binding == "different-credential":
            session._accounts = [Account(
                unique_id="alice", display_name="Synthetic",
                provider_id=session.provider_id, credential_id="PRIVATE_OTHER",
            )]
        elif binding == "two-accounts":
            session._accounts = [original, original]
        else:
            session._accounts = [Account(
                unique_id="bad PRIVATE id", display_name="Synthetic",
                provider_id=session.provider_id, credential_id=session.credential_id,
            )]
        rows = session.search("catalogue", field="title")
        assert paths == ["/api/v2/catalog/search"]
        assert [row.record_id for row in rows] == ["public-edition"]
        assert rows[0].owned is False
        assert rows[0].downloadable is None
        assert rows[0].revision_id is None
        assert rows[0].source_collection == "catalog"
        diagnostic = json.dumps({
            "metadata": rows[0].metadata,
            "rejections": session.catalog.last_search_rejections,
        })
        for secret in ("PRIVATE_", "download_url", "account_id", "revision_id"):
            assert secret not in diagnostic
    finally:
        session.close()


def test_verified_search_keeps_personal_rights_and_explicit_catalogue_is_public():
    session, paths = _case(library_status=200)
    try:
        # A connected session with a matching, strictly validated account can
        # still combine private inventory and public bibliographic results.
        combined = session.search("catalogue")
        assert paths == [
            "/api/v2/catalog/search", "/api/v2/library/search",
        ]
        assert [row.record_id for row in combined] == [
            "public-edition", "owned-edition",
        ]
        assert combined[0].owned is False
        assert combined[1].owned is True
        assert combined[1].downloadable is True
        assert combined[1].revision_id == "revision-1"

        # Even a verified account must not trigger private discovery when the
        # caller explicitly asks for catalogue-only scope.
        paths.clear()
        public = session.search("catalogue", scope="catalog")
        assert paths == ["/api/v2/catalog/search"]
        assert [row.record_id for row in public] == ["public-edition"]
        assert public[0].owned is False
        assert session.catalog.last_search_rejections == []
    finally:
        session.close()


def test_public_only_catalogue_denial_does_not_retry_or_hide_http_error():
    session, paths = _case(catalog_status=403)
    try:
        session.connected = False
        with pytest.raises(KoboVaultError) as exc:
            session.search("catalogue")
        assert exc.value.status_code == 403
        assert "PRIVATE" not in str(exc.value)
        assert paths == ["/api/v2/catalog/search"]
    finally:
        session.close()


def test_catalogue_service_rejects_unknown_scope_without_any_http():
    session, paths = _case()
    try:
        with pytest.raises(ProviderError, match="portée de recherche invalide"):
            session.catalog.search("x", scope="library")
        assert paths == []
    finally:
        session.close()