"""P0 #3196 W5 — a legacy path move cannot rebind a typed Kobo source.

SQLite-only paths, no file I/O or provider calls. This gate prevents an
unattested rename from changing the historical v0.7 receipt of a canonical
edition. It does not rewrite canonical source/placement provenance.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _case(tmp_path):
    store = Storage(tmp_path / "state.sqlite3")
    old = tmp_path / "alice" / "before.epub"
    new = tmp_path / "alice" / "after.epub"
    store.backup_put({
        "provider_id": "kobo", "credential_id": "vault",
        "identity_id": "alice", "account_id": "buyer",
        "item_id": "edition-a", "title": "Synthetic",
        "status": "complete", "local_path": str(old),
    })
    return store, old, new


def _typed(store, *, edition="edition-a", credential="vault",
           owner="alice", buyer="buyer", state="active"):
    with store._lock, store._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,"
            "state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("typed:1", owner, "kobo", credential, buyer,
             "purchase-branch", edition, "purchase", "purchase",
             state, 1, 1),
        )


def _tables(store):
    with store._lock, store._connect() as db:
        return tuple(
            tuple(db.execute("SELECT * FROM " + name + " ORDER BY 1").fetchall())
            for name in ("backup_items", "placements", "catalog_entries",
                         "source_references", "origin_links", "acquisition_ledger",
                         "entitlements", "artifacts")
        )


@pytest.mark.parametrize("owner,buyer,state", [
    ("alice", "buyer", "active"),
    ("alice", "other-buyer", "active"),
    ("bob", "other-buyer", "unknown"),
    ("bob", "other-buyer", "inactive"),
])
def test_legacy_rename_refuses_exact_typed_edition_before_any_sql_change(
    tmp_path, owner, buyer, state,
):
    store, old, new = _case(tmp_path)
    try:
        _typed(store, owner=owner, buyer=buyer, state=state)
        before = _tables(store)
        with pytest.raises(
            ValueError, match="relocate_canonical_source_requires_core_relink",
        ):
            store.relocate_local_path(
                old, new, identity_id="alice", provider_id="kobo",
            )
        assert _tables(store) == before
        assert store.backup_get("kobo", "vault", "edition-a")["local_path"] == str(old)
    finally:
        store.close()


@pytest.mark.parametrize("source", [
    {"edition": "other-edition"},
    {"credential": "other-credential"},
])
def test_unrelated_typed_source_preserves_legacy_rename_compatibility(
    tmp_path, source,
):
    store, old, new = _case(tmp_path)
    try:
        _typed(store, **source)
        before = store.backup_get("kobo", "vault", "edition-a")
        store.relocate_local_path(
            old, new, identity_id="alice", provider_id="kobo",
        )
        updated = store.backup_get("kobo", "vault", "edition-a")
        assert updated["identity_id"] == before["identity_id"] == "alice"
        assert updated["local_path"] == str(new)
        assert len(store.placements()) == 1
        assert store.placements()[0]["path"] == str(new)
    finally:
        store.close()


def test_typed_source_allows_true_noop_but_never_promotes_owner(tmp_path):
    store, old, new = _case(tmp_path)
    try:
        _typed(store)
        original = _tables(store)
        store.relocate_local_path(
            old, old, identity_id="alice", provider_id="kobo",
        )
        # A no-op must not change a typed source, placement or legacy row.
        assert _tables(store) == original
    finally:
        store.close()
