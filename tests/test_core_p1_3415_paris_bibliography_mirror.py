"""#3415: persist only attested Paris search facets for an exact known notice.

Real Core SQLite mirror and local Search; entirely synthetic Paris results.
No HTTP, credential lookup, acquisition or entitlement mutation.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.search_orchestration import mirrored_catalogue_scoped_id
from tests.test_catalog_083 import _service
from tests.test_core_p0_1354_mirrored_source_scope import notice


UPSTREAM_FIELDS = {
    "subjects": "Subj",
    "tags": "Tags",
    "paris_blog_categories": "BlogPostCategories",
    "paris_blog_tags": "BlogPostTags",
}


def _evidence(scenario: str) -> dict[str, dict[str, str]]:
    return {
        key: {
            "source": "paris.syracuse.search",
            "source_field": "Resource." + value,
            "scope": scenario,
            "confidence": "upstream_field_present",
            "freshness": "search_response",
        }
        for key, value in UPSTREAM_FIELDS.items()
    }


def _resource(*, scenario="CATALOGUE", docbase="EDITION-1",
              credential="reader-A", suffix="A"):
    return replace(
        notice(docbase=docbase, credential=credential),
        scenario=scenario,
        metadata={
            "subjects": ["Sujet " + suffix, "Culture"],
            "tags": ["Astronomie" if suffix == "A" else "Géographie"],
            "paris_blog_categories": ["Catégorie " + suffix],
            "paris_blog_tags": ["Blog " + suffix],
            "bibliography_evidence": _evidence(scenario),
            "rights": {"synthetic_unverified": True},
        },
    )


@pytest.mark.parametrize("scenario", ["CATALOGUE", "EBOOKS"])
def test_paris_bibliography_survives_sqlite_and_known_search_by_exact_edition(
    tmp_path, scenario,
):
    service = _service(str(tmp_path))
    try:
        first = _resource(scenario=scenario)
        second = _resource(scenario=scenario, docbase="EDITION-2", suffix="B")
        foreign_identity = _resource(scenario=scenario, credential="reader-B")
        assert service._mirror_remote_results([first, second], identity_id="default") == 2
        assert service._mirror_remote_results([foreign_identity], identity_id="alex") == 1

        saved = service.storage.catalog_entries(provider_id="paris")
        assert len(saved) == 3
        key_a = mirrored_catalogue_scoped_id(
            "default", "paris", first.record_id, "reader-A",
            "EDITION-1", first.source_collection,
        )
        key_b = mirrored_catalogue_scoped_id(
            "default", "paris", second.record_id, "reader-A",
            "EDITION-2", second.source_collection,
        )
        assert key_a and key_b and key_a != key_b
        rows = {row["catalog_entry_id"]: row for row in saved}
        for key, suffix in ((key_a, "A"), (key_b, "B")):
            metadata = rows[key]["metadata"]
            assert metadata["subjects"] == ["Sujet " + suffix, "Culture"]
            assert metadata["tags"] == [
                "Astronomie" if suffix == "A" else "Géographie"
            ]
            assert metadata["paris_blog_categories"] == ["Catégorie " + suffix]
            assert metadata["paris_blog_tags"] == ["Blog " + suffix]
            assert metadata["bibliography_evidence"] == _evidence(scenario)
            assert metadata["source_credential"] == "reader-A"
            assert metadata["docbase"] == ("EDITION-1" if suffix == "A" else "EDITION-2")
            assert metadata["catalogue_scope_version"] == 2
        assert rows[key_a]["identity_id"] == rows[key_b]["identity_id"] == "default"
        assert rows[key_a]["metadata"]["paris_blog_categories"] != rows[key_b]["metadata"]["paris_blog_categories"]

        known = service.search_known_catalog(
            "livre partagé", identity_id="default", provider_ids=["paris"],
        )
        assert len(known) == 2
        assert {r["metadata"]["paris_blog_tags"][0] for r in known} == {"Blog A", "Blog B"}
        subject = service.search_known_catalog(
            "Sujet A", field="subject", identity_id="default", provider_ids=["paris"],
        )
        assert len(subject) == 1
        assert subject[0]["metadata"]["docbase"] == "EDITION-1"
        tag = service.search_known_catalog(
            "Astronomie", identity_id="default", provider_ids=["paris"],
        )
        assert len(tag) == 1 and tag[0]["metadata"]["docbase"] == "EDITION-1"
        other = service.search_known_catalog(
            "livre partagé", identity_id="alex", provider_ids=["paris"],
        )
        assert len(other) == 1
        assert other[0]["metadata"]["source_credential"] == "reader-B"
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
    finally:
        service.close()


@pytest.mark.parametrize("mutation", [
    "wrong_scope", "wrong_source", "extra_evidence",
    "oversized_list", "unsafe_value", "non_paris", "unknown_scenario",
])
def test_reject_unverified_paris_resource_facets_without_inventing_rights(
    tmp_path, mutation,
):
    service = _service(str(tmp_path))
    try:
        source = _resource()
        meta = {**source.metadata}
        proofs = {key: dict(value) for key, value in meta["bibliography_evidence"].items()}
        if mutation == "wrong_scope":
            proofs["paris_blog_categories"]["scope"] = "EBOOKS"
        elif mutation == "wrong_source":
            proofs["paris_blog_categories"]["source_field"] = "Resource.Another"
        elif mutation == "extra_evidence":
            proofs["paris_blog_categories"]["token"] = "should-not-be-copied"
        elif mutation == "oversized_list":
            meta["paris_blog_categories"] = ["name"] * 13
        elif mutation == "unsafe_value":
            meta["paris_blog_categories"] = ["https://example.invalid/?token=untrusted"]
        elif mutation == "non_paris":
            source = replace(source, source_provider="gallica")
        elif mutation == "unknown_scenario":
            source = replace(source, scenario="PNB")
        meta["bibliography_evidence"] = proofs
        source = replace(source, metadata=meta)
        assert service._mirror_remote_results([source], identity_id="default") == 1
        row, = service.storage.catalog_entries(identity_id="default")
        recorded = row["metadata"]
        assert "paris_blog_categories" not in recorded
        if mutation in {"non_paris", "unknown_scenario"}:
            assert "bibliography_evidence" not in recorded
        else:
            assert "paris_blog_categories" not in recorded["bibliography_evidence"]
        assert "token" not in str(recorded.get("bibliography_evidence", ""))
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
    finally:
        service.close()
