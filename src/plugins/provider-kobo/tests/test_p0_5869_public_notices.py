"""P0 #5869: only the real Kobo Vault public endpoint can attest notices.

MockTransport tests use a real KoboSession, KoboCatalogService and
KoboVaultClient. No network, private library GET, acquisition or DRM.
"""
from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from cifereca.core.public_search_projection import project_public_search_result
from cifereca.plugin_api import ProviderSearchResponse
from cifereca_kobo_catalog import KoboCatalogService
from cifereca_kobo_vault import KoboVaultClient
from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


PUBLIC_CARD = {
    "product_id": "public-edition",
    "title": "Public edition",
    "description": "<p>Public description</p>",
    "collection": "library", "owned": True, "downloadable": True,
    "account_id": "SECRET_OTHER_ACCOUNT",
    "revision_id": "SECRET_REVISION",
    "download_url": "https://private.invalid/secret.acsm",
    "acquisition": {"token": "SECRET_ACCESS_TOKEN"},
}


def _session(handler):
    session = session_with_vault(FakeVault([]))
    client = KoboVaultClient(
        "https://vault.example", "SECRET_API_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    session.client = client
    session.catalog = KoboCatalogService(
        client, credential_id=session.credential_id,
        provider_id=session.provider_id,
    )
    return session


def test_catalogue_only_publishes_exact_same_call_inert_public_notice():
    paths = []
    def respond(request):
        paths.append(request.url.path)
        assert request.url.path == "/api/v2/catalog/search"
        return httpx.Response(200, json={"items": [PUBLIC_CARD]})

    session = _session(respond)
    try:
        response = session.search_with_response_facets("public", "catalog")
        assert type(response) is ProviderSearchResponse
        assert paths == ["/api/v2/catalog/search"]
        assert len(response.results) == len(response.public_notices) == 1
        row = response.results[0]
        public_notice = response.public_notices[0].result
        assert public_notice is not row
        assert public_notice.id == row.id
        assert public_notice.source_credential == session.credential_id
        assert public_notice.source_collection == "catalog"
        assert public_notice.owned is public_notice.downloadable is False
        assert public_notice.download_url is public_notice.friendly_url is None
        assert public_notice.thumbnail_url is public_notice.description is None
        assert public_notice.source_account_id is None
        assert public_notice.metadata == {}
        projection = project_public_search_result(
            response, row,
            provider_id=session.provider_id, credential_id=session.credential_id,
        )
        assert projection is not None
        assert projection.metadata == {"catalogue_access": "shared_read_only"}
        assert projection.download_url is projection.friendly_url is None
        assert projection.owned is projection.downloadable is False
        assert response.facets == () and response.partial is False
    finally:
        session.close()


def test_replaced_catalogue_adapter_is_not_a_public_notice_authority():
    session = session_with_vault(FakeVault([]))
    session.catalog.search = lambda *_a, **_k: [
        replace(
            KoboCatalogService(
                session.client, credential_id=session.credential_id,
                provider_id=session.provider_id,
            ).normalize_item({
                "product_id": "fake-edition", "title": "Forged catalogue",
                "collection": "catalog", "owned": False,
            }),
        )
    ]
    try:
        response = session.search_with_response_facets("fake", "catalog")
        assert len(response.results) == 1
        assert response.public_notices == ()
    finally:
        session.close()


def test_private_library_hit_never_gains_public_attestation():
    paths = []
    def respond(request):
        paths.append(request.url.path)
        if request.url.path == "/api/v2/catalog/search":
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/api/v2/library/search":
            return httpx.Response(200, json={"items": [{
                "product_id": "owned-edition", "title": "Private purchase",
                "collection": "library", "owned": True,
                "account_id": "alice",
            }]})
        pytest.fail("unexpected private endpoint")

    session = _session(respond)
    try:
        response = session.search_with_response_facets("private", "all")
        assert response.public_notices == ()
        assert "/api/v2/catalog/search" in paths
    finally:
        session.close()


def test_failed_public_endpoint_does_not_reuse_last_response():
    state = {"fail": False}
    def respond(request):
        assert request.url.path == "/api/v2/catalog/search"
        if state["fail"]:
            return httpx.Response(403)
        return httpx.Response(200, json={"items": [PUBLIC_CARD]})

    session = _session(respond)
    try:
        first = session.search_with_response_facets("first", "catalog")
        assert len(first.public_notices) == 1
        state["fail"] = True
        with pytest.raises(Exception):
            session.search_with_response_facets("second", "catalog")
        assert len(first.public_notices) == 1
        assert first.public_notices[0].result is not first.results[0]
    finally:
        session.close()


@pytest.mark.parametrize("requested,ids,partial", [
    (("epub",), ("epub", "kepub"), True),
    (("kepub",), ("kepub",), True),
    (("pdf",), ("pdf",), True),
    (("mp3",), ("audio",), True),
    (("ebook",), ("epub", "kepub", "pdf", "unknown", "conflict"), False),
    (("audiobook",), ("audio",), False),
    (("epub", "physical"), ("epub", "kepub"), True),
    (("physical",), (), True),
])
def test_catalogue_only_formats_are_bound_to_exact_returned_rows(requested, ids, partial):
    cards = [
        dict(product_id="epub", title="EPUB", format="EPUB", file_format="epub"),
        dict(product_id="kepub", title="KEPUB", format="KEPUB"),
        dict(product_id="pdf", title="PDF", file_format="PDF"),
        dict(product_id="unknown", title="Unknown"),
        dict(product_id="conflict", title="Conflict", format="EPUB", file_format="PDF"),
        dict(product_id="audio", title="Audio", format="MP3", content_type="audiobook"),
    ]
    paths = []

    def respond(request):
        paths.append(request.url.path)
        assert request.url.path == "/api/v2/catalog/search"
        return httpx.Response(200, json={"items": cards})

    session = _session(respond)
    try:
        response = session.search_with_response_facets("format", "catalog", content_types=requested)
        assert tuple(row.record_id for row in response.results) == ids
        assert response.partial is partial
        assert response.catalogue_complete is False and response.terminal_empty is False
        assert len(response.public_notices) == len(response.results)
        for row, declaration in zip(response.results, response.public_notices):
            assert declaration.result is not row
            assert declaration.result.record_id == row.record_id
            assert declaration.result.owned is declaration.result.downloadable is False
            assert declaration.result.download_url is None
        assert paths == ["/api/v2/catalog/search"]
    finally:
        session.close()


@pytest.mark.parametrize("bad", ["epub", (False,), ("",), ("x"*81,), ("epub",)*33])
def test_invalid_catalogue_formats_are_refused_before_http(bad):
    from cifereca.plugin_api import ProviderCapabilityError

    def respond(request):
        pytest.fail("invalid format must not issue HTTP")

    session = _session(respond)
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_content_type"):
            session.search_with_response_facets("format", "catalog", content_types=bad)
    finally:
        session.close()
