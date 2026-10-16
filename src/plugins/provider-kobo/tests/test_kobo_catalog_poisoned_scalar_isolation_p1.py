"""#1346: malformed Kobo catalogue scalar fields cannot abort independent books.

All catalogue adapters are synthetic; no live account, HTTP, acquisition or DRM.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from cifereca_kobo_catalog import KoboCatalogService


class Poison:
    def __bool__(self):
        raise RuntimeError("Bearer PRIVATE_TOKEN /home/PRIVATE_BOOK")

    def __str__(self):
        raise RuntimeError("Bearer PRIVATE_TOKEN /home/PRIVATE_BOOK")

    def __repr__(self):
        raise RuntimeError("Bearer PRIVATE_TOKEN /home/PRIVATE_BOOK")


class ExplodingGet(dict):
    def get(self, key, default=None):
        if key == "title":
            raise RuntimeError("Bearer PRIVATE_TOKEN /home/PRIVATE_BOOK")
        return super().get(key, default)


def catalogue_with(*items):
    class FakeVault:
        base_url = "https://vault.example"
        last_search_merge_rejections = ()

        def search(self, query, **options):
            assert options["scope"] == "all"
            return {"items": list(items)}

    return KoboCatalogService(FakeVault(), credential_id="synthetic")


@pytest.mark.parametrize("field,value", [
    ("title", Poison()),
    ("publisher", Poison()),
    ("format", Poison()),
    ("content_type", Poison()),
    ("collection", Poison()),
    ("publication_date", Poison()),
    ("revision_id", Poison()),
    ("price", Poison()),
    ("currency", Poison()),
    ("price", {"amount": Poison(), "currency": "EUR"}),
])
def test_poisoned_scalar_skips_only_its_own_catalogue_hit(field, value):
    bad = {"product_id": "poisoned", "title": "Private"}
    bad[field] = value
    catalog = catalogue_with(
        {"product_id": "before", "title": "Visible 1"},
        bad,
        {"product_id": "after", "title": "Visible 2"},
    )
    rows = catalog.search("safe")
    assert [row.record_id for row in rows] == ["before", "after"]
    assert [row.title for row in rows] == ["Visible 1", "Visible 2"]
    assert all(row.owned is False and row.downloadable is None for row in rows)
    assert catalog.last_search_rejections == [
        {"type": "ProviderError", "reason": "normalization_error"},
    ]
    exposed = json.dumps(catalog.last_search_rejections)
    for secret in ("PRIVATE", "Bearer", "/home/", "RuntimeError"):
        assert secret not in exposed


def test_mapping_getter_failure_is_quarantined_without_error_class_leak():
    catalog = catalogue_with(
        ExplodingGet({"product_id": "poisoned", "title": "Secret"}),
        {"product_id": "good", "title": "Visible"},
    )
    result = catalog.search("query")
    assert [row.record_id for row in result] == ["good"]
    assert catalog.last_search_rejections == [
        {"type": "ProviderError", "reason": "normalization_error"},
    ]


def test_invalid_product_id_preserves_existing_fixed_diagnostic_code():
    catalog = catalogue_with(
        {"product_id": False, "title": "No edition"},
        {"product_id": "valid", "title": "Visible"},
    )
    rows = catalog.search("query")
    assert [row.record_id for row in rows] == ["valid"]
    assert catalog.last_search_rejections == [
        {"type": "ProviderError", "reason": "missing_product_id"},
    ]


def test_clean_catalogue_search_has_no_spurious_rejection():
    catalog = catalogue_with(
        {"product_id": "first", "title": "Visible"},
        {"product_id": "second", "title": "Visible again"},
    )
    rows = catalog.search("query")
    assert [row.record_id for row in rows] == ["first", "second"]
    assert catalog.last_search_rejections == []
