"""P1 #3185: moving a file cannot invent the owner of legacy SQLite rights.

All paths and acquisition rows are synthetic; relocation here touches SQLite
only, not the filesystem or provider.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _rig(tmp_path):
    store = Storage(tmp_path / "state.sqlite3")
    original = tmp_path / "alice" / "original.epub"
    target = tmp_path / "alice" / "renamed.epub"
    store.backup_put({
        "provider_id": "kobo",
        "credential_id": "card",
        "account_id": "reader",
        "identity_id": "alice",
        "item_id": "book-a",
        "title": "Synthetic",
        "status": "complete",
        "local_path": str(original),
    })
    return store, original, target


def _legacy(store):
    return store.backup_get("kobo", "card", "book-a")


def _unknown_legacy_owner(store):
    # Simulate a restored Kobo 0.7 source with a missing *projection*
    # identity, but an independently attested canonical placement.
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE backup_items SET identity_id=NULL "
            "WHERE provider_id=? AND credential_id=? AND item_id=?",
            ("kobo", "card", "book-a"),
        )


def test_noop_relocation_does_not_promote_null_legacy_owner(tmp_path):
    store, original, _ = _rig(tmp_path)
    try:
        _unknown_legacy_owner(store)
        assert _legacy(store)["identity_id"] is None
        placement_before = store.placements()[0]
        store.relocate_local_path(
            original, original, identity_id="alice", provider_id="kobo",
        )
        assert _legacy(store)["identity_id"] is None
        assert _legacy(store)["local_path"] == str(original)
        placement_after = store.placements()[0]
        for key in ("placement_id", "path", "identity_id", "provider_id",
                    "source_item_id", "artifact_id"):
            assert placement_after[key] == placement_before[key]
    finally:
        store.close()


def test_genuine_move_refuses_unknown_legacy_owner_without_sql_mutation(tmp_path):
    store, original, target = _rig(tmp_path)
    try:
        _unknown_legacy_owner(store)
        before = store.placements(), store.catalog_entries(), _legacy(store)
        with pytest.raises(ValueError, match="relocate_scope_conflict"):
            store.relocate_local_path(
                original, target, identity_id="alice", provider_id="kobo",
            )
        assert (store.placements(), store.catalog_entries(), _legacy(store)) == before
    finally:
        store.close()


def test_scoped_rename_changes_path_but_not_owner(tmp_path):
    store, original, target = _rig(tmp_path)
    try:
        assert _legacy(store)["identity_id"] == "alice"
        store.relocate_local_path(
            original, target, identity_id="alice", provider_id="kobo",
        )
        assert _legacy(store)["identity_id"] == "alice"
        assert _legacy(store)["local_path"] == str(target)
        assert len(store.placements()) == 1
        assert store.placements()[0]["path"] == str(target)
        assert store.placements()[0]["identity_id"] == "alice"
    finally:
        store.close()
