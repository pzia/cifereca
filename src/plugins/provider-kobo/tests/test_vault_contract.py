from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from cifereca.plugin_api import ProviderError, RuntimeAcquisitionCapabilities
from cifereca_kobo import DESCRIPTOR, KoboSession
from cifereca_kobo_catalog import KoboCatalogService
from cifereca_kobo_vault import KoboVaultClient


class Diagnostics:
    def add(self, *_args, **_kwargs):
        return None


def test_vault_public_status_and_authenticated_library_contract():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/v2/status":
            assert request.headers.get("authorization") is None
            return httpx.Response(200, json={
                "ok": True,
                "api_version": "2.3",
                "capabilities": ["catalog_search", "library_search", "library", "acquisition"],
                "capability_details": [],
                "acquisition_access": "configured",
            })

        assert request.headers.get("authorization") == "Bearer secret"
        if request.url.path == "/api/v2/catalog/search":
            assert request.url.params["field"] == "title"
            return httpx.Response(200, json={"items": [
                {"product_id": "p1", "title": "Germinal", "owned": True},
                {"product_id": "p2", "title": "Nana", "owned": False},
            ]})
        if request.url.path == "/api/v2/library/search":
            return httpx.Response(200, json={"items": [
                {"product_id": "p1", "revision_id": "r1", "title": "Germinal", "owned": True},
            ], "next_cursor": None})
        if request.url.path == "/api/v2/library/manifest":
            return httpx.Response(200, json={"items": [
                {"product_id": "p1", "revision_id": "r1", "title": "Germinal", "owned": True},
            ], "next_cursor": None})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "missing"}})

    client = KoboVaultClient("https://vault.example.test", "secret", transport=httpx.MockTransport(handler))
    try:
        assert client.status()["api_version"] == "2.3"
        items = client.search("Germinal", field="title")["items"]
        assert [item["product_id"] for item in items] == ["p1", "p2"]
        assert items[0]["revision_id"] == "r1"
        assert client.library_manifest()[0]["revision_id"] == "r1"
        assert not any("jobs" in path or "/agent/" in path for path in paths)
    finally:
        client.close()


def test_vault_acquisition_sends_current_optional_contract_fields():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/library/p1/acquisition"
        assert request.headers.get("authorization") == "Bearer secret"
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"acquisition": {"candidates": []}})

    client = KoboVaultClient("https://vault.example.test", "secret", transport=httpx.MockTransport(handler))
    try:
        client.acquisition(
            "p1",
            revision_id="r1",
            mode="descriptor",
            display_profile="Desktop",
            include_acsm=True,
        )
        assert seen == {
            "mode": "descriptor",
            "revision_id": "r1",
            "display_profile": "Desktop",
            "include_acsm": True,
        }
        with pytest.raises(ProviderError, match="DisplayProfile invalide"):
            client.acquisition("p1", display_profile="Unsupported")
    finally:
        client.close()


def test_kobo_provider_requires_vault_api_23_or_newer():
    class FakeVault:
        def __init__(self, api_version: str):
            self.api_version = api_version

        def status(self, authenticated=False):
            assert authenticated is False
            return {
                "ok": True,
                "api_version": self.api_version,
                "capabilities": ["acquisition"],
                "capability_details": [],
                "acquisition_access": "configured",
            }

        def account(self):
            return {"id": "acct", "display_name": "Kobo"}

        def close(self):
            return None

    session = KoboSession(
        descriptor=DESCRIPTOR,
        credential_id="vault-1",
        label="Kobo",
        credentials={"api_token": "x"},
        options={"base_url": "https://vault.example"},
        config={},
        storage={},
        diagnostics=Diagnostics(),
        runtime_capabilities=SimpleNamespace(
            acquisition=lambda _provider: RuntimeAcquisitionCapabilities(
                provider_id="kobo",
                available=True,
                compatible=True,
                operational=True,
                modes=("adobe_adept", "kobo_delegated"),
                formats=("epub", "pdf", "kepub"),
            )
        ),
    )
    session.client.close()
    try:
        session.client = FakeVault("2.2")
        with pytest.raises(ProviderError, match=r"v2\.3\+ requise"):
            session.connect()

        session.client = FakeVault("2.3")
        accounts = session.connect()
        assert accounts[0].unique_id == "acct"
        assert session.supports_acquisition_display_profile is True
        assert session.supports_acquisition_acsm is True
    finally:
        session.close()


def test_vault_capabilities_are_intersected_with_runtime_modes():
    class FakeVault:
        def status(self, authenticated=False):
            return {
                "ok": True,
                "api_version": "2.3",
                "capabilities": ["acquisition"],
                "capability_details": [],
                "acquisition_access": "configured",
            }

        def account(self):
            return {"id": "acct", "display_name": "Kobo"}

        def close(self):
            return None

    class Broker:
        def __init__(self, modes):
            self.modes = modes

        def acquisition(self, provider):
            assert provider == "kobo"
            return RuntimeAcquisitionCapabilities(
                provider_id="kobo",
                available=True,
                compatible=True,
                operational=True,
                modes=tuple(self.modes),
            )

    session = KoboSession(
        descriptor=DESCRIPTOR,
        credential_id="vault-1",
        label="Kobo",
        credentials={"api_token": "x"},
        options={"base_url": "https://vault.example"},
        config={},
        storage={},
        diagnostics=Diagnostics(),
        runtime_capabilities=Broker(("adobe_adept",)),
    )
    session.client.close()
    session.client = FakeVault()
    try:
        session.connect()
        assert session.supports_acquisition_acsm is True
        assert session.supports_acquisition_display_profile is False

        session.runtime_capabilities = Broker(("kobo_delegated",))
        session.connect()
        assert session.supports_acquisition_acsm is False
        assert session.supports_acquisition_display_profile is True

        session.runtime_capabilities = Broker(())
        session.connect()
        assert session.supports_acquisition_acsm is False
        assert session.supports_acquisition_display_profile is False
    finally:
        session.close()


def test_kobo_cover_url_normalizes_relative_and_nested_shapes():
    catalog = KoboCatalogService(
        SimpleNamespace(base_url="https://vault.example/base"),
        credential_id="vault",
    )

    assert catalog.cover_url({"cover_url": "/covers/book.jpg"}) == "https://vault.example/covers/book.jpg"
    assert catalog.cover_url({"images": {"medium": {"url": "https://cdn.example/book.jpg"}}}) == "https://cdn.example/book.jpg"

    item = catalog.normalize_item({
        "product_id": "b1",
        "title": "Book",
        "images": [{"thumbnail": "/covers/thumb.jpg"}],
    })
    assert item.thumbnail_url == "https://vault.example/covers/thumb.jpg"


def test_kobo_catalog_reports_rejected_rows_and_resets_between_searches():
    class Client:
        base_url = "https://vault.example"

        def __init__(self):
            self.calls = 0

        def search(self, query, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return {
                    "items": [
                        {"product_id": "ok-1", "title": "Valide"},
                        {"title": "Sans identifiant"},
                        "invalid-shape",
                    ]
                }
            return {"items": [{"product_id": "ok-2", "title": "Valide 2"}]}

    catalog = KoboCatalogService(Client(), credential_id="vault")

    first = catalog.search("test")

    assert [row.record_id for row in first] == ["ok-1"]
    assert catalog.last_search_rejections == [
        {"type": "ProviderError", "reason": "missing_product_id"},
        {"type": "invalid_shape", "reason": "item_not_object"},
    ]

    second = catalog.search("test 2")

    assert [row.record_id for row in second] == ["ok-2"]
    assert catalog.last_search_rejections == []


def test_kobo_diagnostic_state_exposes_catalog_search_rejections():
    session = KoboSession(
        descriptor=DESCRIPTOR,
        credential_id="vault-1",
        label="Kobo",
        credentials={"api_token": "x"},
        options={"base_url": "https://vault.example"},
        config={},
        storage={},
        diagnostics=Diagnostics(),
        runtime_capabilities=SimpleNamespace(acquisition=lambda _provider: None),
    )
    try:
        session.catalog.last_search_rejections = [
            {"type": "ProviderError", "reason": "missing_product_id"}
        ]

        assert session.diagnostic_state()["catalog_search_rejections"] == [
            {"type": "ProviderError", "reason": "missing_product_id"}
        ]
    finally:
        session.close()


def test_kobo_library_keeps_valid_rows_and_resets_rejections():
    class FakeVault:
        base_url = "https://vault.example"

        def __init__(self):
            self.calls = 0
            self.last_manifest_rejections = []

        def library_manifest(self):
            self.calls += 1
            if self.calls == 1:
                self.last_manifest_rejections = [
                    {"type": "invalid_shape", "reason": "item_not_object"},
                ]
                return [
                    {"product_id": "owned-1", "title": "Owned", "owned": True},
                    {"title": "Sans identifiant", "owned": True},
                ]
            self.last_manifest_rejections = []
            return [
                {"product_id": "owned-2", "title": "Owned 2", "owned": True},
            ]

        def close(self):
            return None

    session = KoboSession(
        descriptor=DESCRIPTOR,
        credential_id="vault-1",
        label="Kobo",
        credentials={"api_token": "x"},
        options={"base_url": "https://vault.example"},
        config={},
        storage={},
        diagnostics=Diagnostics(),
        runtime_capabilities=SimpleNamespace(acquisition=lambda _provider: None),
    )
    session.client.close()
    session.client = FakeVault()
    session.catalog = KoboCatalogService(
        session.client,
        credential_id=session.credential_id,
        provider_id=session.provider_id,
    )
    try:
        first = session.library()

        assert [row.record_id for row in first] == ["owned-1"]
        assert session.last_library_rejections == [
            {"type": "invalid_shape", "reason": "item_not_object"},
            {"type": "ProviderError", "reason": "missing_product_id"},
        ]
        assert session.diagnostic_state()["library_rejections"] == session.last_library_rejections

        second = session.library()

        assert [row.record_id for row in second] == ["owned-2"]
        assert session.last_library_rejections == []
        assert session.diagnostic_state()["library_rejections"] == []
    finally:
        session.close()


def test_vault_manifest_reports_non_object_rows():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/library/manifest"
        return httpx.Response(
            200,
            json={
                "items": [
                    {"product_id": "ok-1", "title": "Valid"},
                    "invalid-shape",
                ],
                "next_cursor": None,
            },
        )

    client = KoboVaultClient(
        "https://vault.example",
        "secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        rows = client.library_manifest()

        assert [row["product_id"] for row in rows] == ["ok-1"]
        assert client.last_manifest_rejections == [
            {"type": "invalid_shape", "reason": "item_not_object"},
        ]
    finally:
        client.close()


def test_vault_search_reports_merge_rejections_and_resets(monkeypatch):
    client = KoboVaultClient("https://vault.example", "secret")
    calls = {"round": 0}

    def catalog_search(_query, **_kwargs):
        if calls["round"] == 0:
            return {
                "items": [
                    {"product_id": "c1", "title": "Catalog"},
                    {"title": "Missing id"},
                    "invalid-shape",
                ]
            }
        return {"items": [{"product_id": "c2", "title": "Catalog 2"}]}

    def library_search(_query, **_kwargs):
        if calls["round"] == 0:
            return {"items": None}
        return {"items": [{"product_id": "l2", "title": "Library 2"}]}

    monkeypatch.setattr(client, "search_catalog", catalog_search)
    monkeypatch.setattr(client, "search_library", library_search)

    try:
        first = client.search("test", scope="all")

        assert [row["product_id"] for row in first["items"]] == ["c1"]
        assert client.last_search_merge_rejections == [
            {"source": "catalog", "type": "invalid_shape", "reason": "missing_product_id"},
            {"source": "catalog", "type": "invalid_shape", "reason": "item_not_object"},
            {"source": "library", "type": "invalid_shape", "reason": "items_not_list"},
        ]

        calls["round"] = 1
        second = client.search("test 2", scope="all")

        assert [row["product_id"] for row in second["items"]] == ["c2", "l2"]
        assert client.last_search_merge_rejections == []
    finally:
        client.close()


def test_kobo_catalog_aggregates_vault_merge_rejections():
    class Client:
        base_url = "https://vault.example"
        last_search_merge_rejections = []

        def search(self, _query, **_kwargs):
            self.last_search_merge_rejections = [
                {"source": "library", "type": "invalid_shape", "reason": "missing_product_id"}
            ]
            return {"items": [{"product_id": "ok-1", "title": "Valid"}]}

    catalog = KoboCatalogService(Client(), credential_id="vault")
    rows = catalog.search("test")

    assert [row.record_id for row in rows] == ["ok-1"]
    assert catalog.last_search_rejections == [
        {"source": "library", "type": "invalid_shape", "reason": "missing_product_id"}
    ]
