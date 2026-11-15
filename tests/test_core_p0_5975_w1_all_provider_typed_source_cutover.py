"""P0 #5975 / #3196: typed Core source supersedes every supplier's W1 mirror.

Offline SQLite fixtures exercise the real legacy Storage.backup_put entrypoint.
No provider GET, file materialization, right observation or migration rewrite.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


_CORE = (
    "entitlements", "acquisition_ledger", "artifacts", "placements",
    "catalog_entries", "source_references", "origin_links",
)
_PROVIDER_IDS = ("kobo", "paris", "numilog", "unregistered-supplier")


def _snapshot(store):
    with store._lock, store._connect() as db:
        return {
            name: tuple(db.execute("SELECT * FROM " + name + " ORDER BY 1").fetchall())
            for name in (*_CORE, "backup_items", "metadata")
        }


def _old_row(provider="paris", credential="card", edition="edition", *, owner="alice"):
    return {
        "provider_id": provider, "credential_id": credential,
        "item_id": edition, "account_id": "buyer",
        "identity_id": owner, "entitlement_type": "owned",
        "revision_id": "legacy-1", "title": "Old archive",
        "status": "pending", "sha256": "a" * 64, "size": 111,
        "updated_at": 100,
    }


def _typed_source(store, provider, credential="card", edition="edition",
                  *, owner="bob", account="a-different-borrower"):
    """Only an inert typed A record; never a supplier right or delivery proof."""
    with store._lock, store._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "source:" + provider + ":" + credential + ":" + edition,
                owner, provider, credential, account, "branch-b",
                edition, "right-b", "purchase", 10, 10,
            ),
        )


@pytest.mark.parametrize("provider", _PROVIDER_IDS)
@pytest.mark.parametrize("existing_archive", (False, True))
@pytest.mark.parametrize("source_owner", ("alice", "bob"))
def test_exact_typed_source_blocks_every_legacy_writer_without_mutation(
    tmp_path, provider, existing_archive, source_owner,
):
    store = Storage(tmp_path / "ledger.sqlite3")
    try:
        row = _old_row(provider)
        if existing_archive:
            store.backup_put(row)
        old = store.backup_get(provider, "card", "edition")
        _typed_source(store, provider, owner=source_owner)
        before = _snapshot(store)
        # If a legacy row is present, its status/title/sha are not rewritten;
        # if absent, the old writer cannot create a new conflicting archive.
        for status in ("complete", "error", "acquiring"):
            store.backup_put({
                **row, "status": status, "title": "Foreign old replay",
                "sha256": "b" * 64, "size": 222, "revision_id": "legacy-2",
                "updated_at": 200,
            })
            assert store.backup_get(provider, "card", "edition") == old
            assert _snapshot(store) == before
    finally:
        store.close()


@pytest.mark.parametrize("different_coordinate", (
    "provider", "credential", "edition",
))
def test_source_fence_matches_all_three_coordinates_only(
    tmp_path, different_coordinate,
):
    store = Storage(tmp_path / "ledger.sqlite3")
    try:
        _typed_source(store, "paris")
        row = _old_row("paris")
        if different_coordinate == "provider":
            row["provider_id"] = "numilog"
        elif different_coordinate == "credential":
            row["credential_id"] = "unrelated-card"
        else:
            row["item_id"] = "other-edition"
        old = _snapshot(store)
        store.backup_put(row)
        assert store.backup_get(
            row["provider_id"], row["credential_id"], row["item_id"]
        ) is not None
        # Exact typed-source fencing must not become a global archive veto:
        # an unrelated provider/credential/edition stays writable as 0.7
        # history. Since #6149, even this non-Kobo legacy row is archive-only
        # and cannot synthesize a right, a completed delivery or local bytes.
        assert store.entitlements(provider_id=row["provider_id"]) == []
        assert store.acquisitions(provider_id=row["provider_id"]) == []
        after = _snapshot(store)
        # A new historical backup_items row is allowed for a DIFFERENT
        # SourceKey. No canonical table is changed by that archive insert.
        assert len(after["backup_items"]) == len(old["backup_items"]) + 1
        assert all(after[name] == rows for name, rows in old.items()
                   if name != "backup_items")
    finally:
        store.close()


def test_two_storage_instances_observe_committed_typed_source(tmp_path):
    path = tmp_path / "shared.sqlite3"
    source_owner = Storage(path)
    writer = Storage(path)
    try:
        # Distinct sqlite connections, same exact source. The writer check
        # must see the durable typed source before updating any legacy table.
        _typed_source(source_owner, "unregistered-supplier", owner="bob")
        before = _snapshot(source_owner)
        writer.backup_put(_old_row("unregistered-supplier"))
        assert _snapshot(writer) == before
    finally:
        writer.close()
        source_owner.close()


def test_generic_w1_source_veto_precedes_legacy_sql_writes(tmp_path):
    store = Storage(tmp_path / "ledger.sqlite3")
    try:
        _typed_source(store, "numilog")
        before = _snapshot(store)
        with store._lock, store._connect() as db:
            db.execute("""
                CREATE TRIGGER block_w1_backup_insert BEFORE INSERT
                ON backup_items BEGIN
                    SELECT RAISE(ABORT,'legacy_writer_reached');
                END
            """)
        # If the generic source-fence is bypassed, the trigger aborts.
        store.backup_put(_old_row("numilog"))
        assert _snapshot(store) == before
    finally:
        store.close()
