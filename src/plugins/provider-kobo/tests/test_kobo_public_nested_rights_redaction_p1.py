"""#1346: nested Vault catalogue metadata is never account/rights evidence.

Synthetic HTTP only: no provider account or acquisition.
"""
from __future__ import annotations

import json

import httpx
import pytest

from cifereca_kobo_catalog import KoboCatalogService
from cifereca_kobo_vault import KoboVaultClient


def _poisoned_catalog():
    return {
        "items": [{
            "product_id": "edition-public",
            "title": "Public work",
            "description": "A verified public synopsis",
            "authors": ["Public Author"],
            "subjects": ["Science", "Fiction"],
            "price": {"amount": "9.99", "currency": "EUR"},
            "cover_url": "https://covers.example/public.jpg",
            "owned": True, "downloadable": True, "collection": "library",
            "account_id": "PRIVATE_TOP_ACCOUNT",
            "metadata": {
                "publisher": "Public Publisher",
                "awards": ["Public Award"],
                "ownership": {
                    "isOwned": True, "revisionId": "PRIVATE_NESTED_REV",
                },
                "edition_info": {
                    "rights": {"downloadUrl": "PRIVATE_NESTED_DOWNLOAD"},
                    "aliases": [
                        {"name": "public alternate", "accountId": "PRIVATE_ALIAS_ACCOUNT"},
                        {"license_url": "PRIVATE_LICENSE"},
                    ],
                    "private_store": {
                        "api-token": "PRIVATE_API_TOKEN",
                        "localPath": "/home/PRIVATE_USER/ebooks/secret.epub",
                        "sourceCredential": "PRIVATE_SOURCE",
                        "entitlements": [{"token": "PRIVATE_RIGHTS"}],
                    },
                },
            },
            "variants": [
                {"language": "fr", "sessionToken": "PRIVATE_SESSION"},
                {"language": "en", "acquisition": {"url": "PRIVATE_SIGNED"}},
            ],
        }],
        "page": 1,
        "total": 1,
        "account": {"id": "PRIVATE_ENVELOPE_ACCOUNT"},
        "metadata": {
            "provider": "kobo",
            "credentials": {"accessToken": "PRIVATE_ENVELOPE_TOKEN"},
            "extra": {
                "public_version": "2",
                "download-url": "PRIVATE_ENVELOPE_DOWNLOAD",
            },
        },
        "library": {"items": [{"product_id": "PRIVATE_HIDDEN_EDITION"}]},
    }


@pytest.mark.parametrize("scope", ["catalog", "all"])
def test_public_catalogue_does_not_export_nested_account_rights(scope):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/api/v2/catalog/search":
            return httpx.Response(200, json=_poisoned_catalog())
        if request.url.path == "/api/v2/library/search":
            return httpx.Response(200, json={"items": []})
        pytest.fail(f"unexpected request {request.url.path}")

    vault = KoboVaultClient(
        "https://vault.example", "fixture",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = vault.search("public", scope=scope)
        public = result if scope == "catalog" else result["catalog"]
        assert public["page"] == 1 and public["total"] == 1
        assert public["metadata"]["provider"] == "kobo"
        assert public["metadata"]["extra"]["public_version"] == "2"
        assert "account" not in public and "library" not in public

        row = result["items"][0]
        assert row["product_id"] == "edition-public"
        assert row["title"] == "Public work"
        assert row["authors"] == ["Public Author"]
        assert row["subjects"] == ["Science", "Fiction"]
        assert row["description"] == "A verified public synopsis"
        assert row["price"] == {"amount": "9.99", "currency": "EUR"}
        assert row["metadata"]["publisher"] == "Public Publisher"
        assert row["metadata"]["awards"] == ["Public Award"]
        assert row["metadata"]["edition_info"]["aliases"][0]["name"] == "public alternate"
        assert [entry["language"] for entry in row["variants"]] == ["fr", "en"]
        assert row["collection"] == "catalog"
        assert row["owned"] is False

        projected = KoboCatalogService(vault, credential_id="fixture").normalize_item(row)
        assert projected.owned is False
        assert projected.downloadable is None
        assert projected.revision_id is None
        assert projected.source_collection == "catalog"
        assert projected.description == "A verified public synopsis"
        assert projected.thumbnail_url == "https://covers.example/public.jpg"

        public_json = json.dumps([public, projected.metadata], ensure_ascii=False)
        for secret in (
            "PRIVATE_", "secret.epub", "/home/", "accessToken",
            "sessionToken", "account_id", "accountId",
            "revisionId", "rights", "entitlements", "credentials",
            "downloadUrl", "download-url", "license_url", "localPath",
            "sourceCredential", "acquisition",
        ):
            assert secret not in public_json
        assert seen == (
            ["/api/v2/catalog/search"] if scope == "catalog"
            else ["/api/v2/catalog/search", "/api/v2/library/search"]
        )
    finally:
        vault.close()


def test_public_projection_handles_case_variants_and_excessive_nesting():
    deep = {"accountId": "PRIVATE_DEEP"}
    for _ in range(24):
        deep = {"nested": deep}
    raw = {
        "product_id": "safe", "title": "Visible",
        "ownerShip": {"account-id": "PRIVATE_OWNER"},
        "Account_ID": "PRIVATE_OTHER",
        "META": [{"download_url": "PRIVATE_URL", "public_tag": "tag"}],
        "depth": deep,
    }

    sanitized = KoboVaultClient._public_catalog_hit(raw)
    assert sanitized["owned"] is False
    assert sanitized["collection"] == "catalog"
    assert sanitized["title"] == "Visible"
    assert sanitized["META"] == [{"public_tag": "tag"}]
    assert "PRIVATE" not in json.dumps(sanitized)