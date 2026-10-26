"""#3185: deletion of an artifact authority requires all references absent.

Only synthetic SQLite. No physical files, provider, acquisition or DRM I/O.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _storage(tmp_path):
    store = Storage(tmp_path / "artifact-links.sqlite")
    digest = "a" * 64
    aid = "sha256:" + digest
    store.artifact_put({
        "artifact_id": aid, "sha256": digest, "size": 8,
        "media_format": "epub", "metadata": {},
    })
    return store, aid


@pytest.mark.parametrize("table", [
    "catalog", "acquisition", "variant_derivative", "variant_source", "placement",
])
def test_active_artifact_reference_prevents_authority_deletion(tmp_path, table):
    storage, aid = _storage(tmp_path)
    if table == "catalog":
        storage.catalog_entry_put({
            "catalog_entry_id": "synthetic-orphaned-catalog",
            "artifact_id": aid, "placement_id": None,
            "title": "Synthetic book", "identity_id": "synthetic-owner",
            "provider_id": "local", "item_id": "book-a", "local_state": "missing",
            "metadata": {"source": "synthetic"},
        })
    elif table == "acquisition":
        storage.acquisition_put({
            "acquisition_id": "synthetic-receipt",
            "provider_id": "numilog", "credential_id": "synthetic-account",
            "account_id": "synthetic-reader", "identity_id": "synthetic-owner",
            "item_id": "book-a", "status": "complete",
            "artifact_id": aid, "metadata": {},
        })
    elif table in {"variant_derivative", "variant_source"}:
        storage.artifact_variant_put({
            "variant_id": "synthetic-variant",
            "artifact_id": aid if table == "variant_derivative" else "sha256:" + "b" * 64,
            "source_artifact_id": aid if table == "variant_source" else "sha256:" + "b" * 64,
            "source_sha256": "b" * 64,
            "transformer_plugin_id": "synthetic-transformation",
            "options_fingerprint": "synthetic",
        })
    else:
        storage.placement_put({
            "artifact_id": aid, "path": str(tmp_path / "missing.epub"),
            "identity_id": "synthetic-owner", "provider_id": "local",
            "source_item_id": "book-a", "state": "missing", "metadata": {},
        })

    assert storage.artifact_delete_if_unreferenced(aid) is False
    assert storage.artifact_get(aid)["artifact_id"] == aid


def test_orphan_artifact_can_still_be_removed(tmp_path):
    storage, aid = _storage(tmp_path)
    assert storage.artifact_delete_if_unreferenced(aid) is True
    assert storage.artifact_get(aid) is None


def test_receipt_ref_protects_artifact_after_placement_is_detached(tmp_path):
    storage, aid = _storage(tmp_path)
    placement = storage.placement_put({
        "artifact_id": aid, "path": str(tmp_path / "disappeared.epub"),
        "identity_id": "synthetic-owner", "provider_id": "numilog",
        "source_item_id": "edition-a", "state": "missing", "metadata": {},
    })
    storage.acquisition_put({
        "acquisition_id": "receipt-a", "provider_id": "numilog",
        "credential_id": "synthetic-account", "identity_id": "synthetic-owner",
        "item_id": "edition-a", "status": "complete",
        "artifact_id": aid, "metadata": {"provenance": "synthetic"},
    })
    storage.placement_delete(placement)
    assert storage.placement_get(placement) is None
    assert storage.artifact_delete_if_unreferenced(aid) is False
    assert storage.artifact_get(aid) is not None
