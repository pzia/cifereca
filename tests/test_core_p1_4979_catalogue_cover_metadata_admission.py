"""P1 #4979: malformed catalogue cover provenance must not abort projections.

Real Core Library/Recent projections, disposable SQLite and synthetic files.
No provider network, catalogue refresh, entitlement write or GTK invocation.
"""
from __future__ import annotations

import pytest

from tests.test_core_library_placement_identity_scope import _service
from tests.test_core_p0_2797_originlink_library_projection import _local_artifact


GOOD_ORIGIN = "https://covers.example.org/attested-origin.png"
GOOD_CATALOGUE = "https://covers.example.org/attested-catalogue.png"


class Uncoercible:
    def __str__(self):
        raise AssertionError("catalogue provenance is not a string")

    def __int__(self):
        raise AssertionError("catalogue strength is not a number")

    def __repr__(self):
        return "UNTRUSTED"


@pytest.mark.parametrize("broken_field", [
    {"source": ["not-a-source"]},
    {"source": Uncoercible()},
    {"evidence_id": {"unexpected": "mapping"}},
    {"evidence_id": Uncoercible()},
    {"evidence_strength": "not-an-integer"},
    {"evidence_strength": Uncoercible()},
    {"evidence_strength": True},
    {"evidence_strength": 1001},
    {"evidence_strength": float("nan")},
])
def test_actual_local_library_and_recent_ignore_only_malformed_cover(
    tmp_path, monkeypatch, broken_field,
):
    service = _service(tmp_path)
    try:
        artifact_id, placement_id = _local_artifact(
            service, tmp_path, owner="alice",
        )
        catalog_row, = service.storage.catalog_entries()
        metadata = {
            **catalog_row["metadata"],
            "catalog_cover": {
                "url": GOOD_CATALOGUE,
                "source": "public-catalogue",
                "evidence_id": "edition:exact",
                "evidence_strength": 55,
                **broken_field,
            },
        }
        original = service.storage.catalog_entries
        monkeypatch.setattr(
            service.storage, "catalog_entries",
            lambda: [{**row, "metadata": metadata} for row in original()],
        )

        # One malformed catalogue cover is *not* a failed Library/Recent
        # request, nor a reason to invent a selected/complete cover.
        library, = service.library_entries(identity_id="alice", scan=False)
        recent, = service.recent_acquisitions(identity_id="alice")
        for row in (library, recent):
            assert row["identity_id"] == "alice"
            assert row["artifact_id"] == artifact_id
            assert row["placement_id"] == placement_id
            assert row["thumbnail_url"] is None
            assert row["metadata"]["cover_resolution"]["status"] == "missing"

        # Keep an independent verified origin cover when catalogue metadata
        # is malformed, without a new GET or any change to its provenance.
        metadata["cover_url"] = GOOD_ORIGIN
        library, = service.library_entries(identity_id="alice", scan=False)
        recent, = service.recent_acquisitions(identity_id="alice")
        for row in (library, recent):
            assert row["thumbnail_url"] == GOOD_ORIGIN
            assert row["metadata"]["cover_resolution"]["tier"] == "origin"
            assert row["metadata"]["cover_resolution"]["source"] == "catalog-entry"
    finally:
        service.close()


@pytest.mark.parametrize("broken_field", [
    {"source": {"unexpected": "supplier object"}},
    {"source": Uncoercible()},
    {"evidence_id": ["not-a-source-id"]},
    {"evidence_id": Uncoercible()},
    {"evidence_strength": "invalid"},
    {"evidence_strength": Uncoercible()},
    {"evidence_strength": False},
    {"evidence_strength": float("inf")},
])
def test_actual_remote_entitlement_keeps_independent_origin_and_exact_scope(
    tmp_path, monkeypatch, broken_field,
):
    service = _service(tmp_path)
    try:
        metadata = {
            "title": "Synthetic remote ebook",
            "catalog_cover": {
                "url": GOOD_CATALOGUE, "source": "public-catalogue",
                "evidence_id": "remote:exact", "evidence_strength": 50,
                **broken_field,
            },
        }
        entitlement = {
            "provider_id": "numilog", "identity_id": "alice",
            "credential_id": "cred-a", "account_id": "borrower-a",
            "item_id": "edition-a", "entitlement_type": "owned",
            "state": "active", "metadata": metadata,
        }
        monkeypatch.setattr(
            service.storage, "entitlements", lambda: [entitlement],
        )
        alice, = service.library_entries(identity_id="alice", scan=False)
        assert alice["local"] is False
        assert alice["identity_id"] == "alice"
        assert alice["thumbnail_url"] is None
        assert alice["metadata"]["cover_resolution"]["status"] == "missing"
        assert not service.library_entries(identity_id="bob", scan=False)

        metadata["cover_url"] = GOOD_ORIGIN
        alice, = service.library_entries(identity_id="alice", scan=False)
        assert alice["thumbnail_url"] == GOOD_ORIGIN
        assert alice["metadata"]["cover_resolution"]["tier"] == "origin"
        assert alice["metadata"]["cover_resolution"]["source"] == "entitlement"
        assert alice["possessions"][0]["account_id"] == "borrower-a"
        assert not service.library_entries(identity_id="bob", scan=False)
    finally:
        service.close()


def test_valid_catalogue_cover_still_selected_with_original_scalar_evidence(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _local_artifact(service, tmp_path, owner="alice")
        catalog_row, = service.storage.catalog_entries()
        metadata = {
            **catalog_row["metadata"],
            "catalog_cover": {
                "url": GOOD_CATALOGUE, "source": "public-catalogue",
                "evidence_id": "edition:exact", "evidence_strength": 55,
            },
        }
        original = service.storage.catalog_entries
        monkeypatch.setattr(
            service.storage, "catalog_entries",
            lambda: [{**row, "metadata": metadata} for row in original()],
        )
        for row in (
            service.library_entries(identity_id="alice", scan=False)[0],
            service.recent_acquisitions(identity_id="alice")[0],
        ):
            resolution = row["metadata"]["cover_resolution"]
            assert row["thumbnail_url"] == GOOD_CATALOGUE
            assert resolution["status"] == "selected"
            assert resolution["tier"] == "catalog"
            assert resolution["source"] == "public-catalogue"
            assert resolution["evidence_id"] == "edition:exact"
            assert resolution["candidate_count"] == 1
    finally:
        service.close()
