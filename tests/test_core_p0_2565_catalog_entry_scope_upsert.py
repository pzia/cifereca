"""P0 #2565: an upsert must not transfer a local catalogue link.

SQLite-only synthetic identities, placements and editions.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _entry(placement_id, **changes):
    row = {
        "catalog_entry_id": "local-book",
        "placement_id": placement_id,
        "identity_id": "account-a",
        "provider_id": "kobo",
        "item_id": "edition-a",
        "artifact_id": "sha256:synthetic",
        "title": "Example book",
        "media_format": "pdf",
        "local_state": "local",
        "metadata": {"path": "/synthetic/book.pdf"},
    }
    row.update(changes)
    return row


@pytest.mark.parametrize("changes", [
    {"placement_id": "other-placement"},
    {"placement_id": None},
    {"identity_id": "account-b"},
    {"provider_id": "numilog"},
    {"item_id": "edition-b"},
])
def test_existing_local_catalogue_scope_cannot_be_reassigned(tmp_path, changes):
    storage = Storage(tmp_path / "db.sqlite")
    storage.catalog_entry_put(_entry("original-placement"))
    original = storage.catalog_entry_get("local-book")

    proposed = _entry("original-placement")
    proposed.update(changes)
    with pytest.raises(ValueError, match="catalog_entry_scope_conflict"):
        storage.catalog_entry_put(proposed)

    assert storage.catalog_entry_get("local-book") == original
    assert len(storage.catalog_entries()) == 1


def test_same_placement_allows_regular_refresh_and_missing_transition(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    storage.catalog_entry_put(_entry("original-placement"))
    storage.catalog_entry_put(_entry(
        "original-placement", title="Updated title",
        local_state="missing", metadata={"reason": "offline"},
    ))
    row = storage.catalog_entry_get("local-book")
    assert row["placement_id"] == "original-placement"
    assert row["identity_id"] == "account-a"
    assert row["provider_id"] == "kobo"
    assert row["item_id"] == "edition-a"
    assert row["title"] == "Updated title"
    assert row["local_state"] == "missing"


def test_remote_catalogue_entry_can_refresh_without_local_placement(tmp_path):
    storage = Storage(tmp_path / "db.sqlite")
    storage.catalog_entry_put(_entry(None, local_state="remote_catalog"))
    storage.catalog_entry_put(_entry(
        None, provider_id="numilog", item_id="edition-b",
        local_state="remote_catalog",
    ))
    row = storage.catalog_entry_get("local-book")
    assert row["provider_id"] == "numilog"
    assert row["item_id"] == "edition-b"
    assert row["placement_id"] is None
