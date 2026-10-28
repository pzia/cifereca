from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from cifereca.plugin_api import Account, ProviderError, RuntimeAcquisitionCapabilities
from cifereca_kobo import DESCRIPTOR, KoboSession
from cifereca_kobo_catalog import KoboCatalogService
from cifereca_kobo_vault import KoboVaultClient


class Diagnostics:
    def add(self, *_args, **_kwargs):
        return None


def test_untrusted_vault_status_never_exposes_free_text_or_private_endpoint():
    class Capture:
        def __init__(self):
            self.details = []

        def add(self, *_args, **kwargs):
            self.details.append(kwargs.get("detail"))

    class FakeVault:
        base_url = "https://person:PRIVATE_VALUE@vault.example/private?token=PRIVATE_VALUE"

        def status(self, *, authenticated=False):
            assert not authenticated
            return {
                "api_version": "2.3",
                "release": "2.3.3 PRIVATE_VALUE",
                "role": "person=PRIVATE_VALUE",
                "acquisition_access": "PRIVATE_VALUE",
                "capabilities": ["acquisition", "catalog_search", "PRIVATE_VALUE", 123],
                "capability_details": [
                    {"id": "acquisition", "available": True,
                     "description": "person=PRIVATE_VALUE", "count": 9},
                    {"id": "PRIVATE_VALUE", "enabled": True},
                ],
            }

        def account(self):
            return {"id": "fixture-account", "display_name": "Synthetic Kobo"}

        def close(self):
            return None

    capture = Capture()
    session = KoboSession(
        descriptor=DESCRIPTOR,
        credential_id="credential-fixture",
        label="Kobo",
        credentials={"api_token": "fixture"},
        options={"base_url": "https://vault.example"},
        config={}, storage={}, diagnostics=capture,
        runtime_capabilities=SimpleNamespace(
            acquisition=lambda _provider: RuntimeAcquisitionCapabilities(
                provider_id="kobo", available=True, compatible=True,
                operational=True, modes=("kobo_delegated",),
            ),
        ),
    )
    session.client.close()
    session.client = FakeVault()
    try:
        session.connect()
        diagnostics = session.diagnostic_state()
        assert session.supports_acquisition_display_profile is True
        assert session.capability_names == {"acquisition", "catalog_search"}
        assert session.capability_details == {
            "acquisition": {"id": "acquisition", "available": True},
        }
        assert diagnostics["vault_url"] == "configured"
        assert diagnostics["acquisition_access"] == "unknown"
        assert diagnostics["vault_release"] is None
        assert capture.details
        assert capture.details[0]["credential_configured"] is True
        assert "credential_id" not in capture.details[0]
        assert capture.details[0]["role"] == "kobo-library-provider"
        assert capture.details[0]["capabilities"] == ["acquisition", "catalog_search"]
        serialized = json.dumps(
            [diagnostics, *capture.details], ensure_ascii=False,
        )
        assert "PRIVATE_VALUE" not in serialized
        assert "person:" not in serialized
        assert "private?token=" not in serialized
    finally:
        session.close()


def test_malformed_vault_api_version_is_not_reflected_in_exception():
    class FakeVault:
        def status(self, *, authenticated=False):
            return {
                "api_version": "2.2?token=PRIVATE_VALUE",
                "capabilities": [],
            }

        def close(self):
            return None

    session = KoboSession(
        descriptor=DESCRIPTOR, credential_id="fixture",
        label="Kobo", credentials={"api_token": "fixture"},
        options={"base_url": "https://vault.example"},
        config={}, storage={}, diagnostics=Diagnostics(),
    )
    session.client.close()
    session.client = FakeVault()
    try:
        with pytest.raises(ProviderError, match="version API incompatible") as err:
            session.connect()
        assert "PRIVATE_VALUE" not in str(err.value)
        assert "inconnue" in str(err.value)
    finally:
        session.close()


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
    session.connected = True
    session._accounts = [Account(
        unique_id="fixture-account", provider_id="kobo",
        credential_id=session.credential_id, display_name="Synthetic Kobo",
    )]
    session.catalog = KoboCatalogService(
        session.client,
        credential_id=session.credential_id,
        provider_id=session.provider_id,
    )
    session.connected = True
    session._accounts = [Account(
        unique_id="fixture-account", display_name="Fixture",
        provider_id="kobo", credential_id=session.credential_id,
    )]
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


def test_kobo_connect_does_not_keep_stale_vault_capabilities():
    class FakeVault:
        def __init__(self):
            self.calls = 0

        def status(self, authenticated=False):
            self.calls += 1
            if self.calls == 1:
                return {
                    "ok": True,
                    "api_version": "2.3",
                    "capabilities": ["acquisition"],
                    "capability_details": [{"id": "acquisition", "enabled": True}],
                    "acquisition_access": "configured",
                }
            return {
                "ok": True,
                "api_version": "2.3",
                "capabilities": "malformed",
                "capability_details": None,
                "acquisition_access": "unknown",
            }

        def account(self):
            return {"id": "acct", "display_name": "Kobo"}

        def close(self):
            return None

    class Broker:
        def acquisition(self, provider):
            assert provider == "kobo"
            return RuntimeAcquisitionCapabilities(
                provider_id="kobo",
                available=True,
                compatible=True,
                operational=True,
                modes=("adobe_adept", "kobo_delegated"),
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
        runtime_capabilities=Broker(),
    )
    session.client.close()
    session.client = FakeVault()
    try:
        session.connect()
        assert session.capability_names == {"acquisition"}
        assert "acquisition" in session.capability_details
        assert session.supports_acquisition_acsm is True
        assert session.supports_acquisition_display_profile is True

        session.connect()

        assert session.capability_names == set()
        assert session.capability_details == {}
        assert session.supports_acquisition_acsm is False
        assert session.supports_acquisition_display_profile is False
    finally:
        session.close()


def test_kobo_connect_succeeds_when_diagnostics_sink_fails():
    class FailingDiagnostics:
        def add(self, *_args, **_kwargs):
            raise RuntimeError("diagnostics unavailable")

    class FakeVault:
        def status(self, authenticated=False):
            assert authenticated is False
            return {
                "ok": True,
                "api_version": "2.3",
                "capabilities": [],
                "capability_details": [],
                "acquisition_access": "unknown",
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
        diagnostics=FailingDiagnostics(),
        runtime_capabilities=SimpleNamespace(acquisition=lambda _provider: None),
    )
    session.client.close()
    session.client = FakeVault()
    try:
        accounts = session.connect()

        assert session.connected is True
        assert [account.unique_id for account in accounts] == ["acct"]
    finally:
        session.close()


def test_kobo_all_scope_keeps_catalogue_description_and_facts_for_owned_manifest(monkeypatch):
    """A thin Kobo library hit must not erase public catalogue search evidence."""
    client = KoboVaultClient("https://vault.example", "secret")

    def catalog(_query, **_opts):
        return {"items": [
            {
                "product_id": "owned-book",
                "title": "Prix du Quai",
                "description": "<p>Finaliste du Prix du Quai des Orfèvres.</p>",
                "subjects": ["polar", "prix littéraire"],
                "series": "Enquêtes",
                "awards": ["Prix du Quai des Orfèvres"],
                "publisher": "Éditions témoin",
                "cover_url": "https://cdn.example/cover.jpg",
                "language": "fr",
                "price": {"amount": "3.00", "currency": "EUR"},
                "owned": False,
                "downloadable": False,
            },
            {"product_id": "catalog-only", "title": "Livre externe", "description": "Notice"},
        ]}

    def library(_query, **_opts):
        return {"items": [
            {
                "product_id": "owned-book",
                "title": "Prix du Quai (bibliothèque)",
                "description": "",
                "subjects": [],
                "awards": None,
                "cover_url": "",
                "owned": True,
                "downloadable": True,
                "archived": False,
                "revision_id": "revision-library",
                "price": None,
            },
            {"product_id": "library-only", "title": "Livre uniquement dans mon compte", "owned": True},
        ]}

    monkeypatch.setattr(client, "search_catalog", catalog)
    monkeypatch.setattr(client, "search_library", library)
    try:
        merged = client.search("prix du quai", scope="all")
        assert [row["product_id"] for row in merged["items"]] == [
            "owned-book", "catalog-only", "library-only",
        ]
        owned = merged["items"][0]
        assert owned["title"] == "Prix du Quai (bibliothèque)"
        assert owned["description"].startswith("<p>Finaliste")
        assert owned["subjects"] == ["polar", "prix littéraire"]
        assert owned["awards"] == ["Prix du Quai des Orfèvres"]
        assert owned["series"] == "Enquêtes"
        assert owned["cover_url"] == "https://cdn.example/cover.jpg"
        assert owned["owned"] is True and owned["downloadable"] is True
        assert owned["revision_id"] == "revision-library"
        assert owned["price"] is None  # never resurrect catalog commercial state

        service = KoboCatalogService(client, credential_id="main")
        result = service.normalize_item(owned)
        assert "Finaliste du Prix du Quai des Orfèvres." in result.description
        assert result.owned and result.downloadable
        assert result.metadata["awards"] == ["Prix du Quai des Orfèvres"]
        assert result.metadata["series"] == "Enquêtes"
        assert result.metadata["subjects"] == ["polar", "prix littéraire"]
        assert result.metadata["revision_id"] == "revision-library"
        assert result.metadata["price"] is None
        assert "description" not in result.metadata
    finally:
        client.close()


def test_kobo_library_explicit_bibliographic_value_wins_and_no_cross_product_merge(monkeypatch):
    """No field may cross an unrelated product id or override a meaningful account value."""
    client = KoboVaultClient("https://vault.example", "secret")
    monkeypatch.setattr(client, "search_catalog", lambda *_a, **_kw: {"items": [
        {"product_id": "a", "description": "Catalogue A", "subjects": ["Série 1"], "owned": False},
        {"product_id": "b", "description": "Catalogue B", "owned": False},
    ]})
    monkeypatch.setattr(client, "search_library", lambda *_a, **_kw: {"items": [
        {"product_id": "a", "description": "Correction bibliothèque", "subjects": ["Série 2"],
         "owned": True, "downloadable": False, "archived": True},
    ]})
    try:
        rows = client.search("x", scope="all")["items"]
        assert len(rows) == 2
        assert rows[0]["description"] == "Correction bibliothèque"
        assert rows[0]["subjects"] == ["Série 2"]
        assert rows[0]["owned"] is True
        assert rows[0]["downloadable"] is False
        assert rows[0]["archived"] is True
        assert rows[1]["description"] == "Catalogue B"
        assert rows[1]["owned"] is False
    finally:
        client.close()


def test_manifest_missing_items_is_not_an_authoritative_empty_response():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        assert request.url.path == "/api/v2/library/manifest"
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"next_cursor": None})
        if calls == 2:
            return httpx.Response(200, json={"items": None, "next_cursor": None})
        return httpx.Response(200, json={
            "items": [{"product_id": "owned", "title": "Owned"}],
            "next_cursor": None,
        })

    client = KoboVaultClient(
        "https://vault.example", "secret", transport=httpx.MockTransport(handler),
    )
    try:
        assert client.library_manifest() == []
        assert client.last_manifest_rejections == [
            {"type": "invalid_shape", "reason": "items_not_list"},
        ]
        assert client.library_manifest() == []
        assert client.last_manifest_rejections[0]["reason"] == "items_not_list"
        assert client.library_manifest()[0]["product_id"] == "owned"
        assert client.last_manifest_rejections == []
    finally:
        client.close()


def test_backup_manifest_reports_only_rejected_row_count_to_core():
    class FakeVault:
        base_url = "https://vault.example"
        last_manifest_rejections = []

        def __init__(self):
            self.calls = 0

        def library_manifest(self):
            self.calls += 1
            if self.calls == 1:
                self.last_manifest_rejections = [
                    {"type": "invalid_shape", "reason": "item_not_object",
                     "private": "Bearer SECRET"},
                ]
            else:
                self.last_manifest_rejections = []
            return [{"product_id": "owned", "title": "Owned"}]

        def close(self):
            return None

    session = KoboSession(
        descriptor=DESCRIPTOR, credential_id="vault-1", label="Kobo",
        credentials={"api_token": "dummy"},
        options={"base_url": "https://vault.example"},
        config={}, storage={}, diagnostics=Diagnostics(),
        runtime_capabilities=SimpleNamespace(acquisition=lambda _: None),
    )
    session.client.close()
    session.client = FakeVault()
    session.connected = True
    session._accounts = [Account(
        unique_id="fixture-account", provider_id="kobo",
        credential_id=session.credential_id, display_name="Synthetic Kobo",
    )]
    session.catalog = KoboCatalogService(
        session.client, credential_id=session.credential_id,
        provider_id=session.provider_id,
    )
    try:
        assert session.backup_manifest()[0]["product_id"] == "owned"
        assert session.backup_manifest_rejection_count == 1
        assert session.backup_manifest()[0]["product_id"] == "owned"
        assert session.backup_manifest_rejection_count == 0
        assert not hasattr(session, "backup_manifest_rejection_messages")
    finally:
        session.close()


@pytest.mark.parametrize(
    "owned, downloadable, default_collection, expected_owned, expected_downloadable",
    [
        (True, True, "catalog", True, True),
        (False, False, "library", False, False),
        (None, None, "library", True, None),
        (None, None, "catalog", False, None),
        ("false", "false", "catalog", False, None),
        ("true", "true", "catalog", False, None),
        ("false", "true", "library", False, None),
        ("true", 1, "library", False, None),
        (1, 1, "catalog", False, None),
        (0, 0, "library", False, None),
        ({}, [], "library", False, None),
    ],
)
def test_kobo_vault_nonboolean_flags_never_escalate_ownership_or_download(
    owned, downloadable, default_collection, expected_owned, expected_downloadable,
):
    """Unexpected Vault JSON shapes fail closed instead of bool("false")."""
    catalog = KoboCatalogService(
        SimpleNamespace(base_url="https://vault.example"), credential_id="vault",
    )
    raw = {
        "product_id": "book-1", "title": "Purchased book?",
        "owned": owned, "downloadable": downloadable,
    }
    result = catalog.normalize_item(raw, default_collection=default_collection)
    assert result.owned is expected_owned
    assert result.downloadable is expected_downloadable
    if default_collection == "catalog" and owned is not True:
        assert result.source_collection == "catalog"


def test_kobo_personal_manifest_malformed_owned_flag_cannot_create_sqlite_right(
    tmp_path,
):
    """A personal-library row with explicit 'false' must not grant ownership."""
    from cifereca.config import Config
    from cifereca.core.kobo_manifest_rights import mirror_kobo_manifest_rights
    from cifereca.diagnostics import Diagnostics
    from cifereca.services import LibraryService
    from cifereca.storage import Storage

    catalog = KoboCatalogService(
        SimpleNamespace(base_url="https://vault.example"), credential_id="vault",
    )
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set("identities", [
        {"id": "alice", "label": "Alice", "directory_name": "alice"},
    ])
    config.set("providers.accounts", [])
    config.save()
    service = LibraryService(
        config, Storage(tmp_path / "library.sqlite3"), Diagnostics(),
    )
    try:
        bad = catalog.normalize_item({
            "product_id": "not-purchased", "title": "Not purchased",
            "owned": "false", "downloadable": "false",
        }, default_collection="library")
        assert bad.owned is False
        assert bad.downloadable is None
        assert mirror_kobo_manifest_rights(
            service, [bad], identity_id="alice",
            credential_id="vault", account_id="account-1",
        ) == 0
        assert service.storage.entitlements(provider_id="kobo") == []
        assert service.library_entries(identity_id="alice") == []

        valid = catalog.normalize_item({
            "product_id": "purchased", "title": "Purchased",
            "owned": True, "downloadable": True,
        }, default_collection="library")
        assert mirror_kobo_manifest_rights(
            service, [valid], identity_id="alice",
            credential_id="vault", account_id="account-1",
        ) == 1
        assert len(service.storage.entitlements(provider_id="kobo")) == 1
    finally:
        service.close()


@pytest.mark.parametrize("failed_stage", ["status", "account"])
def test_failed_reconnect_preserves_delegation_denial_until_authenticated_success(failed_stage):
    """#1350: a failed reconnect must not trigger another forbidden delegated GET."""
    class FakeVault:
        def __init__(self):
            self.failed_stage = None

        def status(self, *, authenticated=False):
            assert authenticated is False
            if self.failed_stage == "status":
                raise ProviderError("synthetic status unavailable")
            return {
                "api_version": "2.3",
                "capabilities": ["acquisition"],
                "acquisition_access": "configured",
            }

        def account(self):
            if self.failed_stage == "account":
                raise ProviderError("synthetic authentication unavailable")
            return {"id": "same-account", "display_name": "Kobo"}

        def close(self):
            pass

    session = KoboSession(
        descriptor=DESCRIPTOR,
        credential_id="scoped-vault", label="Kobo",
        credentials={"api_token": "fixture"},
        options={"base_url": "https://vault.example"},
        config={}, storage={}, diagnostics=Diagnostics(),
    )
    session.client.close()
    vault = FakeVault()
    session.client = vault
    try:
        session.connect()
        session._cifereca_delegation_forbidden = True
        session._cifereca_delegation_attempts_saved = 17

        vault.failed_stage = failed_stage
        with pytest.raises(ProviderError):
            session.connect()
        # Failure is not an authorization update, including when session
        # remains connected from its previous successful account handshake.
        assert session._cifereca_delegation_forbidden is True
        assert session._cifereca_delegation_attempts_saved == 17

        vault.failed_stage = None
        session.connect()
        assert session.connected is True
        assert session._cifereca_delegation_forbidden is False
        assert session._cifereca_delegation_attempts_saved == 0
    finally:
        session.close()

def test_authenticated_archived_manifest_without_owned_flag_still_projects_right(tmp_path):
    """#1337: archive collection is not evidence against a personal right."""
    from cifereca.config import Config
    from cifereca.core.kobo_manifest_rights import mirror_kobo_manifest_rights
    from cifereca.diagnostics import Diagnostics as CoreDiagnostics
    from cifereca.services import LibraryService
    from cifereca.storage import Storage

    catalog = KoboCatalogService(
        SimpleNamespace(base_url="https://vault.example"),
        credential_id="vault-archive",
    )
    raw = {
        "product_id": "archive-1", "title": "Archived personal book",
        "collection": "archived", "archived": True,
        "downloadable": False,
        # Kobo personal manifests can omit the explicit owned property.
    }
    personal = catalog.normalize_item(raw, default_collection="library")
    assert personal.source_collection == "archived"
    assert personal.owned is True
    assert personal.metadata["archived"] is True
    # The same raw record received through public catalog normalization must
    # NOT assert a user's purchase or fabricate an active ownership right.
    public = catalog.normalize_item(raw, default_collection="catalog")
    assert public.owned is False
    explicitly_unowned = catalog.normalize_item(
        {**raw, "owned": False}, default_collection="library",
    )
    assert explicitly_unowned.owned is False
    malformed = catalog.normalize_item(
        {**raw, "owned": "true"}, default_collection="library",
    )
    assert malformed.owned is False

    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set("identities", [
        {"id": "alice", "label": "Alice", "directory_name": "alice"},
    ])
    config.set("providers.accounts", [])
    service = LibraryService(
        config, Storage(tmp_path / "catalog.sqlite3"), CoreDiagnostics(),
    )
    try:
        assert mirror_kobo_manifest_rights(
            service, [personal], identity_id="alice",
            credential_id="vault-archive", account_id="authenticated",
        ) == 1
        assert mirror_kobo_manifest_rights(
            service, [public, explicitly_unowned, malformed], identity_id="alice",
            credential_id="vault-archive", account_id="authenticated",
        ) == 0
        library = service.library_entries(identity_id="alice")
        assert len(library) == 1
        assert library[0]["state"] == "remote_archived"
        assert library[0]["local"] is False
        assert library[0]["local_path"] is None
        assert library[0]["possessions"][0]["account_id"] == "authenticated"
    finally:
        service.close()


def test_kobo_all_search_preserves_authenticated_library_only_origin_without_public_right():
    """#3252: personal-only thin rows do not silently become catalogue notices.

    Search membership, not a public title/ISBN or a forged catalog collection,
    is the sole basis for the inherited library provenance; no delivery right.
    """
    client = KoboVaultClient("https://vault.example", "synthetic-token")
    public_reads = []
    personal_reads = []

    def catalog(query, **kwargs):
        public_reads.append(query)
        return {"items": [
            {"product_id": "public-only", "title": "Public",
             "collection": "library", "owned": True, "downloadable": True},
            {"product_id": "shared", "title": "Public shared"},
        ]}

    def personal(query, **kwargs):
        personal_reads.append(query)
        return {"items": [
            {"product_id": "personal-only", "title": "Private only"},
            {"product_id": "explicit-unowned", "title": "Not owned",
             "owned": False},
            {"product_id": "shared", "title": "Private shared"},
        ]}

    client.search_catalog = catalog
    client.search_library = personal
    try:
        rows = client.search("synthetic", scope="all")["items"]
        service = KoboCatalogService(client, credential_id="credential-A")
        normalized = {
            row["product_id"]: service.normalize_item(row) for row in rows
        }
        assert public_reads == personal_reads == ["synthetic"]
        assert [row["product_id"] for row in rows] == [
            "public-only", "shared", "personal-only", "explicit-unowned",
        ]
        assert rows[2]["collection"] == "library"
        assert rows[1]["collection"] == "library"
        assert normalized["personal-only"].owned is True
        assert normalized["personal-only"].source_collection == "library"
        assert normalized["shared"].owned is True
        assert normalized["public-only"].owned is False
        assert normalized["public-only"].source_collection == "catalog"
        assert normalized["explicit-unowned"].owned is False
        assert all(row.download_url is None for row in normalized.values())
        assert all(row.downloadable is not True for row in normalized.values())
    finally:
        client.close()


def test_kobo_public_only_search_never_promotes_forged_library_collection():
    client = KoboVaultClient("https://vault.example", "synthetic-token")
    client.search_catalog = lambda *_a, **_kw: {"items": [
        {"product_id": "public", "collection": "library",
         "owned": True, "downloadable": True},
    ]}
    client.search_library = lambda *_a, **_kw: (_ for _ in ()).throw(
        AssertionError("catalogue-only must not issue personal GET")
    )
    try:
        rows = client.search("synthetic", scope="catalog")["items"]
        assert len(rows) == 1
        service = KoboCatalogService(client, credential_id="credential-A")
        public = service.normalize_item(rows[0])
        assert public.source_collection == "catalog"
        assert public.owned is False and public.downloadable is not True
    finally:
        client.close()
