"""P0 #2565: batch catalogue upserts preserve the local placement scope.

No network; synthetic SQLite rows only.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _entry(entry_id, placement_id, **changes):
    row = {
        "catalog_entry_id": entry_id,
        "placement_id": placement_id,
        "identity_id": "profile-a",
        "provider_id": "kobo",
        "item_id": "purchase-a",
        "artifact_id": "sha256:fake",
        "title": "Synthetic book",
        "media_format": "pdf",
        "local_state": "local",
        "metadata": {},
    }
    row.update(changes)
    return row


@pytest.mark.parametrize("conflict", [
    {"placement_id": "other"},
    {"placement_id": None},
    {"identity_id": "profile-b"},
    {"provider_id": "numilog"},
    {"item_id": "purchase-b"},
])
def test_batch_rejects_local_transfer_without_partial_writes(tmp_path, conflict):
    store = Storage(tmp_path / "store.sqlite")
    store.catalog_entry_put(_entry("old", "known"))
    original = store.catalog_entry_get("old")
    invalid = _entry("old", "known")
    invalid.update(conflict)

    # The first row is valid, but the second conflicts: neither may commit.
    with pytest.raises(ValueError, match="catalog_entry_scope_conflict"):
        store.catalog_entries_upsert_many([
            _entry("new", "new-placement"), invalid,
        ])

    assert store.catalog_entry_get("old") == original
    assert store.catalog_entry_get("new") is None


def test_repeated_local_id_in_one_batch_cannot_switch_scope(tmp_path):
    store = Storage(tmp_path / "store.sqlite")
    with pytest.raises(ValueError, match="catalog_entry_scope_conflict"):
        store.catalog_entries_upsert_many([
            _entry("fresh", "first"),
            _entry("fresh", "second"),
        ])
    assert store.catalog_entries() == []


def test_batch_allows_local_metadata_and_remote_catalogue_refresh(tmp_path):
    store = Storage(tmp_path / "store.sqlite")
    store.catalog_entry_put(_entry("local", "physical"))
    store.catalog_entry_put(_entry("remote", None, local_state="remote_catalog"))
    ids = store.catalog_entries_upsert_many([
        _entry("local", "physical", title="Better title", local_state="missing"),
        _entry("remote", None, provider_id="numilog", item_id="other",
               local_state="remote_catalog"),
    ])
    assert ids == ["local", "remote"]
    local = store.catalog_entry_get("local")
    assert local["placement_id"] == "physical"
    assert local["title"] == "Better title"
    assert local["local_state"] == "missing"
    remote = store.catalog_entry_get("remote")
    assert remote["provider_id"] == "numilog"
    assert remote["item_id"] == "other"
    assert remote["placement_id"] is None
