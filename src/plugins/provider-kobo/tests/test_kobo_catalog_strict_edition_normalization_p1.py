"""#1346: direct Kobo catalogue normalization must not coerce ambiguous IDs.

Synthetic data only, no HTTP, Core mutations, downloads or DRM.
"""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import ProviderError
from cifereca_kobo_catalog import KoboCatalogService


class FakeClient:
    base_url = "https://vault.example"
    last_search_merge_rejections = []

    def __init__(self, rows=()):
        self.rows = list(rows)

    def search(self, *_args, **_kwargs):
        return {"items": list(self.rows)}


@pytest.mark.parametrize("raw", [
    {"product_id": "edition-A", "id": "edition-B"},
    {"product_id": 123, "id": "edition-A"},
    {"product_id": False, "id": "edition-A"},
    {"product_id": " edition-A", "id": "edition-A"},
    {"product_id": "edition-A\nPRIVATE_TOKEN", "id": "edition-A"},
    {"product_id": "edition A", "id": "edition-A"},
    {"product_id": [], "id": "edition-A"},
])
def test_direct_normalization_rejects_unverifiable_identity_before_rights(raw):
    catalog = KoboCatalogService(FakeClient(), credential_id="PRIVATE_CREDENTIAL")
    with pytest.raises(ProviderError) as exc:
        catalog.normalize_item({
            **raw, "owned": True, "downloadable": True,
            "account_id": "PRIVATE_ACCOUNT",
            "title": "PRIVATE_TITLE",
        })
    for secret in ("PRIVATE_", "edition-A", "edition-B", "TOKEN"):
        assert secret not in str(exc.value)


def test_search_rejects_conflicting_aliases_without_exporting_private_rows():
    catalog = KoboCatalogService(FakeClient([
        {"product_id": "edition-A", "id": "edition-B", "owned": True,
         "title": "PRIVATE_TITLE_A", "account_id": "PRIVATE_ACCOUNT"},
        {"product_id": 123, "id": "edition-A", "owned": True,
         "title": "PRIVATE_TITLE_B"},
        {"id": "legacy-valid", "owned": False, "title": "Public legacy"},
        {"product_id": "both-valid", "id": "both-valid",
         "owned": True, "title": "Valid personal"},
    ]), credential_id="PRIVATE_CREDENTIAL")
    result = catalog.search("test")
    assert [r.record_id for r in result] == ["legacy-valid", "both-valid"]
    assert result[0].owned is False
    assert result[1].owned is True
    assert catalog.last_search_rejections == [
        {"type": "ProviderError", "reason": "missing_product_id"},
        {"type": "ProviderError", "reason": "missing_product_id"},
    ]
    diagnostics = json.dumps(catalog.last_search_rejections)
    assert "PRIVATE" not in diagnostics


def test_valid_legacy_and_matching_aliases_are_compatible():
    catalog = KoboCatalogService(FakeClient(), credential_id="credential")
    assert catalog.normalize_item({"id": "legacy-id"}).record_id == "legacy-id"
    assert catalog.normalize_item({
        "product_id": "same", "id": "same",
    }).record_id == "same"

@pytest.mark.parametrize("owned", [True, False])
def test_kobo_unqualified_age_keys_cannot_impersonate_attested_ranges(owned):
    """Catalogue JSON is not an attested exact-edition age source (#4530)."""
    catalog = KoboCatalogService(FakeClient(), credential_id="credential")
    item = catalog.normalize_item({
        "product_id": "edition-123",
        "title": "Livre jeunesse",
        "owned": owned,
        "recommended_age": 12,
        "youth_age_ranges": [{"min_age": 12, "max_age": 15}],
        "attested_age_ranges": [{"min_age": 12, "max_age": 15}],
        "attested_age_status": "attested",
        "genre": "Jeunesse",
        "typicalAgeRange": "12-15",
    })
    for reserved in (
        "recommended_age", "youth_age_ranges",
        "attested_age_ranges", "attested_age_status",
    ):
        assert reserved not in item.metadata
    # Non-authoritative descriptive fields survive unchanged, but do not
    # generate a canonical numeric age or an ISBN for the selected edition.
    assert item.metadata["genre"] == "Jeunesse"
    assert item.metadata["typicalAgeRange"] == "12-15"
    assert "isbn_digital" not in item.metadata
    assert item.owned is owned


def test_kobo_age_is_not_inferred_from_category_or_title():
    catalog = KoboCatalogService(FakeClient(), credential_id="credential")
    item = catalog.normalize_item({
        "id": "edition-456", "title": "Dès 8 ans",
        "category": "Jeunesse", "genre": "Young adult",
    })
    assert not any(key in item.metadata for key in (
        "recommended_age", "youth_age_ranges",
        "attested_age_ranges", "attested_age_status",
    ))

