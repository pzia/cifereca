"""P0 #1143: malformed autonomous bundles cannot persist private validation text."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca import catalog
from cifereca.plugin_api import BundleError


SECRET = "https://private.invalid/LOAN/book.acsm?auth=PRIVATE_BUNDLE_TOKEN"


class HostileBundleError(BundleError):
    def __str__(self):
        raise AssertionError("Do not stringify private bundle validation failures")
    def __repr__(self):
        raise AssertionError("Do not repr private bundle validation failures")


@pytest.mark.parametrize("error", [
    BundleError(SECRET),
    HostileBundleError(SECRET),
])
def test_invalid_bundle_catalogue_row_keeps_only_closed_reason(
    tmp_path, monkeypatch, error,
):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    saved = []
    storage = NS(
        placement_for_path=lambda path: None,
        placement_put=lambda row: saved.append(dict(row)),
    )
    index = catalog.LocalCatalog(storage, bundle)

    def invalid_manifest(root, *, verify):
        assert root == bundle and verify is True
        raise error

    monkeypatch.setattr(catalog, "load_bundle_manifest", invalid_manifest)
    result = index._index_bundle(bundle, item_id="book-fixture")
    assert result == {
        "status": "invalid", "path": str(bundle),
        "reason": "bundle_manifest_invalid", "artifact_kind": "bundle",
    }
    assert len(saved) == 1
    assert saved[0]["state"] == "invalid"
    assert saved[0]["source_item_id"] == "book-fixture"
    assert saved[0]["metadata"] == {
        "invalid_reason": "bundle_manifest_invalid", "artifact_kind": "bundle",
    }
    assert "PRIVATE_BUNDLE_TOKEN" not in json.dumps(saved) + json.dumps(result)
    assert "private.invalid" not in json.dumps(saved) + json.dumps(result)


def test_invalid_bundle_never_attempts_artifact_or_catalogue_publication(
    tmp_path, monkeypatch,
):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    storage = NS(
        placement_for_path=lambda path: None,
        placement_put=Mock(),
        artifact_put=Mock(side_effect=AssertionError("Invalid bundle must not publish")),
        catalog_entry_put=Mock(side_effect=AssertionError("Invalid bundle must not index")),
    )
    index = catalog.LocalCatalog(storage, bundle)
    monkeypatch.setattr(
        catalog, "load_bundle_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BundleError(SECRET)),
    )
    result = index._index_bundle(bundle)
    assert result["status"] == "invalid"
    storage.placement_put.assert_called_once()
    storage.artifact_put.assert_not_called()
    storage.catalog_entry_put.assert_not_called()
