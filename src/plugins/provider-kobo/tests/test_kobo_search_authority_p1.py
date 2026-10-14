"""P1 #1346: public Kobo catalogue data never supplies personal sync rights.

All HTTP calls are mocked: no real Kobo account, acquisition or filesystem.
"""
from __future__ import annotations

import json

import httpx
import pytest

from cifereca_kobo_catalog import KoboCatalogService
from cifereca_kobo_vault import KoboVaultClient


def _client(monkeypatch, catalog, library):
    def no_http(_request):
        pytest.fail("catalogue/library merge must use only the supplied fixtures")
    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(no_http),
    )
    monkeypatch.setattr(
        client, "search_catalog", lambda *_a, **_k: {"items": catalog},
    )
    monkeypatch.setattr(
        client, "search_library", lambda *_a, **_k: {"items": library},
    )
    return client


def test_thin_personal_hit_only_inherits_public_bibliography_not_catalogue_rights(
    monkeypatch,
):
    catalog = [{
        "product_id": "same-book",
        "title": "Catalogue title",
        "description": "<p>Valid public synopsis</p>",
        "subjects": ["fiction"],
        "cover_url": "https://cdn.example/public.jpg",
        # Deliberately poison every provider/account authority field.
        "owned": True, "downloadable": True, "collection": "catalog",
        "account_id": "PRIVATE_OTHER_ACCOUNT",
        "revision_id": "PRIVATE_OTHER_REVISION",
        "acquisition": {"url": "https://PRIVATE/?token=PRIVATE_TOKEN"},
        "entitlements": [{"owner": "PRIVATE_OWNER"}],
        "source_credential": "PRIVATE_OTHER_CREDENTIAL",
        "price": {"amount": "1000", "currency": "EUR"},
    }]
    library = [{
        "product_id": "same-book",
        "title": "Personal title",
        "description": "",  # Enrich only this known bibliographic gap.
    }]
    client = _client(monkeypatch, catalog, library)
    try:
        rows = client.search("fiction", scope="all")["items"]
        assert len(rows) == 1
        merged = rows[0]
        assert merged["title"] == "Personal title"
        assert merged["description"] == "<p>Valid public synopsis</p>"
        assert merged["subjects"] == ["fiction"]
        assert merged["cover_url"] == "https://cdn.example/public.jpg"
        assert merged["collection"] == "library"
        # The personal endpoint did NOT attest any download flag or revision.
        assert "owned" not in merged
        assert "downloadable" not in merged
        assert "revision_id" not in merged
        assert "account_id" not in merged
        assert "acquisition" not in merged
        assert "entitlements" not in merged
        assert "source_credential" not in merged
        assert "price" not in merged

        normalized = KoboCatalogService(client, credential_id="scoped").normalize_item(
            merged,
        )
        assert normalized.owned is True  # Only personal-library membership.
        assert normalized.downloadable is None
        assert normalized.revision_id is None
        assert normalized.source_collection == "library"
        assert normalized.description == "Valid public synopsis"
        public = json.dumps(merged) + json.dumps(normalized.metadata)
        for private in ("PRIVATE", "OTHER_ACCOUNT", "OTHER_REVISION", "TOKEN"):
            assert private not in public
    finally:
        client.close()


def test_explicit_personal_denial_overrides_catalogue_rights(monkeypatch):
    catalog = [{
        "id": "same-book", "owned": True, "downloadable": True,
        "collection": "library", "revision_id": "PRIVATE_PUBLIC_REV",
        "description": "Public context",
    }]
    library = [{
        "product_id": "same-book", "owned": False, "downloadable": False,
        "collection": "archived", "revision_id": "actual-revision",
    }]
    client = _client(monkeypatch, catalog, library)
    try:
        merged = client.search("archived")["items"][0]
        assert merged["owned"] is False
        assert merged["downloadable"] is False
        assert merged["collection"] == "archived"
        assert merged["revision_id"] == "actual-revision"
        assert merged["description"] == "Public context"
        item = KoboCatalogService(client, credential_id="scoped").normalize_item(merged)
        assert item.owned is False
        assert item.downloadable is False
        assert item.revision_id == "actual-revision"
    finally:
        client.close()


@pytest.mark.parametrize("poisoned", [
    {"product_id": "victim", "id": "unrelated", "description": "PRIVATE_POISON"},
    {"product_id": 123, "id": "victim", "description": "PRIVATE_POISON"},
    {"product_id": " victim", "description": "PRIVATE_POISON"},
    {"product_id": "victim\\nPRIVATE_TOKEN", "description": "PRIVATE_POISON"},
])
def test_conflicting_catalogue_product_aliases_cannot_enrich_account_record(
    monkeypatch, poisoned,
):
    client = _client(monkeypatch, [poisoned], [
        {"product_id": "victim", "owned": False, "description": ""},
    ])
    try:
        response = client.search("victim", scope="all")
        assert response["items"] == [{
            "product_id": "victim", "owned": False, "description": "",
        }]
        assert client.last_search_merge_rejections == [{
            "source": "catalog", "type": "invalid_shape",
            "reason": "missing_product_id",
        }]
        assert "PRIVATE" not in json.dumps(client.last_search_merge_rejections)
    finally:
        client.close()


def test_matching_legacy_alias_can_enrich_without_changing_personal_facts(
    monkeypatch,
):
    client = _client(monkeypatch, [
        {"id": "edition-1", "description": "Verified public description",
         "downloadable": True, "owned": True},
    ], [
        {"id": "edition-1", "product_id": "edition-1",
         "owned": True, "downloadable": False,
         "revision_id": "rev-private"},
    ])
    try:
        merged = client.search("edition-1")["items"][0]
        assert merged["description"] == "Verified public description"
        assert merged["owned"] is True and merged["downloadable"] is False
        assert merged["revision_id"] == "rev-private"
        assert client.last_search_merge_rejections == []
    finally:
        client.close()
