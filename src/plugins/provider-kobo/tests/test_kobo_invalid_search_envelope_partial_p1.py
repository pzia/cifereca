"""#1917 Kobo: malformed result shapes and quarantined rows aren't true zeros.

Pure mock clients, with no real Kobo account, HTTP, acquisition or DRM.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import ProviderError
from cifereca_kobo_catalog import KoboCatalogService
from tests.test_kobo_manifest_policy_p1 import session_with_vault

SECRET = "PRIVATE_SIGNED_URL?token=PRIVATE_SESSION"
WARNING = "partial_results : notices Kobo invalides ignorées"


class FakeVault:
    base_url = "https://vault.example"

    def __init__(self, payload, rejections=None):
        self.payload = payload
        self.last_search_merge_rejections = rejections or []
        self.requests = []

    def search(self, query, **kwargs):
        self.requests.append((query, kwargs))
        return self.payload

    def close(self):
        pass


@pytest.mark.parametrize("payload", [
    {}, {"status": "ok"}, {"items": None}, {"items": False},
    {"items": {"PRIVATE_ACCOUNT": SECRET}}, {"items": "PRIVATE_BOOK"},
    {"items": ("PRIVATE_BOOK",)},
])
@pytest.mark.parametrize("scope", ["all", "catalog"])
def test_malformed_upstream_success_never_proves_empty_catalogue(payload, scope):
    client = FakeVault(payload)
    service = KoboCatalogService(client, credential_id="test")
    with pytest.raises(ProviderError, match="invalid_search_response") as exc:
        service.search("Doyle", scope=scope)
    assert SECRET not in str(exc.value)
    assert len(client.requests) == 1


def test_explicit_empty_items_is_legitimate_empty_success():
    client = FakeVault({"items": []})
    service = KoboCatalogService(client, credential_id="test")
    assert service.search("Doyle", scope="catalog") == []
    assert service.last_search_rejections == []


def test_rejected_library_branch_on_zero_combined_rows_is_partial():
    client = FakeVault({"items": []}, [
        {"source": "library", "type": "invalid_shape", "reason": "items_not_list",
         "message": SECRET},
    ])
    provider = session_with_vault(client)
    try:
        assert provider.search("Doyle", scope="all") == []
        assert provider.last_search_warnings == [WARNING]
        assert SECRET not in repr(provider.last_search_warnings)
        assert len(client.requests) == 1
        assert client.requests[0][1]["scope"] == "all"
    finally:
        provider.close()


def test_catalogue_quarantine_does_not_turn_failed_page_into_exact_zero():
    client = FakeVault({"items": [
        {"product_id": "valid", "title": "Public title"},
        {"title": SECRET, "product_id": 1},
    ]})
    provider = session_with_vault(client, connected=False)
    try:
        rows = provider.search("Doyle", scope="catalog")
        assert [row.title for row in rows] == ["Public title"]
        assert rows[0].owned is False
        assert provider.last_search_warnings == [WARNING]
        assert SECRET not in json.dumps([row.metadata for row in rows])
    finally:
        provider.close()


def test_normal_unrejected_catalogue_is_not_spuriously_partial():
    client = FakeVault({"items": [
        {"product_id": "valid", "title": "Public title"},
    ]})
    provider = session_with_vault(client, connected=False)
    try:
        assert len(provider.search("Doyle", scope="catalog")) == 1
        assert provider.last_search_warnings == []
    finally:
        provider.close()


def test_rejections_not_list_do_not_execute_custom_iterator():
    class Poison:
        def __iter__(self):
            raise AssertionError(SECRET)
    client = FakeVault({"items": []})
    provider = session_with_vault(client)
    try:
        provider.catalog.last_search_rejections = Poison()
        # A real KoboCatalogService.search clears its own rejections; emulate
        # an alternate catalogue service returning a stale arbitrary object.
        provider.catalog = SimpleNamespace(
            search=lambda *a, **k: [],
            last_search_rejections=Poison(),
        )
        assert provider.search("Doyle", scope="catalog") == []
        assert provider.last_search_warnings == []
    finally:
        provider.close()
