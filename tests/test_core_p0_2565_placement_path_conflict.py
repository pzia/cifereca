"""#2565: a path collision must preserve scoped ownership and its real SQLite key.

Only synthetic SQLite rows and paths; no user data or provider requests.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _row(path, **overrides):
    item = {
        "path": str(path),
        "artifact_id": "sha256:previous",
        "identity_id": "account-A",
        "provider_id": "kobo",
        "source_item_id": "edition-A",
        "state": "present",
        "metadata": {"file_size": 123},
    }
    item.update(overrides)
    return item


def test_existing_path_returns_canonical_placement_id(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    path = tmp_path / "example.epub"
    initial = storage.placement_put(_row(path, placement_id="legacy-placement-id"))
    assert initial == "legacy-placement-id"

    # An independent caller derives a different default ID for the same
    # path. The ON CONFLICT(path) upsert MUST return the persisted ID.
    reused = storage.placement_put(_row(path, artifact_id="sha256:updated"))
    assert reused == initial
    assert storage.placement_get(initial)["artifact_id"] == "sha256:updated"
    assert len(storage.placements()) == 1
    assert storage.placement_get(reused)["path"] == str(path)

    storage.catalog_entry_put({
        "placement_id": reused,
        "artifact_id": "sha256:updated",
        "title": "Example",
        "identity_id": "account-A",
        "provider_id": "kobo",
        "item_id": "edition-A",
        "local_state": "local",
        "metadata": {"path": str(path)},
    })
    assert storage.catalog_entries()[0]["placement_id"] == initial


@pytest.mark.parametrize("conflict", [
    {"identity_id": "account-B"},
    {"provider_id": "numilog"},
    {"source_item_id": "edition-B"},
])
def test_conflicting_scope_cannot_reassign_existing_path(tmp_path, conflict):
    storage = Storage(tmp_path / "db.sqlite")
    path = tmp_path / "owned.epub"
    placement_id = storage.placement_put(_row(path, placement_id="original"))
    original = storage.placement_get(placement_id)

    with pytest.raises(ValueError, match="placement_scope_conflict"):
        storage.placement_put(_row(
            path, placement_id="different", artifact_id="sha256:foreign",
            metadata={"file_size": 999}, **conflict,
        ))

    assert storage.placement_get(placement_id) == original
    assert len(storage.placements()) == 1


def test_compatible_refresh_preserves_existing_nonempty_scope(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    path = tmp_path / "owned.epub"
    placement_id = storage.placement_put(_row(path, placement_id="original"))

    returned = storage.placement_put(_row(
        path, placement_id="different", identity_id=None,
        provider_id=None, source_item_id=None,
        artifact_id="sha256:fresh",
    ))

    assert returned == placement_id
    row = storage.placement_get(placement_id)
    assert row["identity_id"] == "account-A"
    assert row["provider_id"] == "kobo"
    assert row["source_item_id"] == "edition-A"
    assert row["artifact_id"] == "sha256:fresh"
