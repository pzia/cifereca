"""#3415: Core mirror persists only evidence-backed Paris Resource taxonomy.

No provider connection or GET: real SQLite re-read for two editions and
two scenarios, with negative/hostile shapes and identity isolation.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.search_orchestration import (
    _safe_paris_bibliography_metadata, _mirrored_catalogue_key,
)
from tests.test_catalog_083 import _service
from tests.test_core_p0_1354_mirrored_source_scope import notice


UPSTREAM = {
    "subjects": "Resource.Subj",
    "tags": "Resource.Tags",
    "paris_blog_categories": "Resource.BlogPostCategories",
    "paris_blog_tags": "Resource.BlogPostTags",
}


def _metadata(scenario: str, *, prefix: str = "Document"):
    values = {
        "subjects": [f"{prefix} Écologie"],
        "tags": [f"{prefix} Nature"],
        "paris_blog_categories": [f"{prefix} Sélection"],
        "paris_blog_tags": [f"{prefix} Club"],
    }
    values["bibliography_evidence"] = {
        key: {
            "source": "paris.syracuse.search",
            "source_field": upstream,
            "scope": scenario,
            "confidence": "upstream_field_present",
            "freshness": "search_response",
        }
        for key, upstream in UPSTREAM.items()
    }
    values["account"] = "unrelated-raw-provider-field"
    values["rights"] = "no_entitlement_from_facets"
    return values


@pytest.mark.parametrize("scenario,medium", [
    ("CATALOGUE", "physical"), ("EBOOKS", "digital"),
])
def test_metadata_for_each_paris_scenario_survives_sqlite_and_search(
    tmp_path, scenario, medium,
):
    service = _service(str(tmp_path))
    try:
        a = replace(
            notice(credential="reader-A", docbase="SYRACUSE" if medium == "physical" else "DILICOM"),
            medium=medium, scenario=scenario, metadata=_metadata(scenario, prefix="Alice"),
        )
        b = replace(
            notice(credential="reader-B", docbase=a.docbase, collection="B-collection"),
            medium=medium, scenario=scenario, metadata=_metadata(scenario, prefix="Bob"),
        )
        assert _mirrored_catalogue_key("default", a)[0] != _mirrored_catalogue_key("default", b)[0]
        assert service._mirror_remote_results([a, b], identity_id="default") == 2
        rows = service.storage.catalog_entries(identity_id="default", provider_id="paris")
        assert len(rows) == 2
        for row in rows:
            meta = row["metadata"]
            expected_prefix = "Alice" if meta["source_credential"] == "reader-A" else "Bob"
            assert meta["subjects"] == [expected_prefix + " Écologie"]
            assert meta["tags"] == [expected_prefix + " Nature"]
            assert meta["paris_blog_categories"] == [expected_prefix + " Sélection"]
            assert meta["paris_blog_tags"] == [expected_prefix + " Club"]
            assert meta["bibliography_evidence"]["paris_blog_categories"]["source_field"] == (
                "Resource.BlogPostCategories"
            )
            assert meta["bibliography_evidence"]["subjects"]["scope"] == scenario
            assert "account" not in meta
        # Search only content already indexed as bibliographic subject/tags.
        matched = service.search_known_catalog("Écologie", identity_id="default")
        assert len(matched) == 2
        assert service.search_known_catalog("Écologie", identity_id="alex") == []
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
    finally:
        service.close()


def test_mirror_never_reinterprets_bad_evidence_or_raw_nested_json(tmp_path):
    service = _service(str(tmp_path))
    try:
        data = _metadata("CATALOGUE")
        data["subjects"] = {"private": "Fake Secret In Subject"}
        data["tags"] = ["https://example.invalid/?token=not-a-subject"]
        data["paris_blog_categories"] = ["<script>alert(1)</script>"]
        data["paris_blog_tags"] = ["One"] * 13
        data["bibliography_evidence"]["paris_blog_categories"]["scope"] = "EBOOKS"
        a = replace(notice(), scenario="CATALOGUE", metadata=data)
        assert service._mirror_remote_results([a], identity_id="default") == 1
        (row,) = service.storage.catalog_entries(identity_id="default", provider_id="paris")
        meta = row["metadata"]
        for field in (*UPSTREAM, "bibliography_evidence", "account"):
            assert field not in meta
        assert "Fake Secret In Subject" not in repr(meta)
        assert not service.search_known_catalog("Fake Secret In Subject", identity_id="default")
    finally:
        service.close()


def test_evidence_schema_requires_exact_field_scope_and_bounded_values():
    valid = _metadata("CATALOGUE")
    accepted = _safe_paris_bibliography_metadata(valid, "CATALOGUE")
    assert set(accepted) == {*UPSTREAM, "bibliography_evidence"}
    assert _safe_paris_bibliography_metadata(valid, "EBOOKS") == {}
    invalid = _metadata("CATALOGUE")
    invalid["bibliography_evidence"]["subjects"]["additional"] = "payload"
    accepted = _safe_paris_bibliography_metadata(invalid, "CATALOGUE")
    assert "subjects" not in accepted
    assert "paris_blog_categories" in accepted
    invalid = _metadata("CATALOGUE")
    invalid["bibliography_evidence"]["subjects"]["source_field"] = "Resource.Series"
    assert "subjects" not in _safe_paris_bibliography_metadata(invalid, "CATALOGUE")
