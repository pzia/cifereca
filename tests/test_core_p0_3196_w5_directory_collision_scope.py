"""#3196 W5: directory migrations cannot merge different Core or legacy sources.

The direct Storage commit uses disposable SQLite and synthetic filenames.
No identity-directory migration filesystem copy, unlink or provider call.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage
from cifereca.identities.directory_migration import JOURNAL_KEY


@pytest.fixture
def rig(tmp_path):
    db = Storage(tmp_path / "state.sqlite3")
    db.identity_put({
        "identity_id": "alice", "directory_name": "alice",
        "label": "Alice",
    })
    old = tmp_path / "alice" / "old.epub"
    new = tmp_path / "alice" / "new.epub"
    a = "sha256:" + "a" * 64
    b = "sha256:" + "b" * 64
    for digest in (a, b):
        db.artifact_put({
            "artifact_id": digest, "sha256": digest[7:],
            "size": 32, "media_format": "epub",
        })
    old_id = db.placement_put({
        "path": str(old), "identity_id": "alice",
        "artifact_id": a, "state": "present",
        "metadata": {"relative_path": "alice/old.epub"},
    })
    new_id = db.placement_put({
        "path": str(new), "identity_id": None,
        "artifact_id": a, "state": "present",
        "metadata": {"relative_path": "alice/new.epub"},
    })
    for cid, pid in (("old", old_id), ("new", new_id)):
        db.catalog_entry_put({
            "catalog_entry_id": "catalogue:" + cid,
            "placement_id": pid, "identity_id": "alice",
            "artifact_id": a, "title": cid,
            "local_state": "local", "metadata": {},
        })
    try:
        yield db, old, new, old_id, new_id, a, b
    finally:
        db.close()


def _plan(rig):
    _, old, new, *_ = rig
    return {
        "root": str(old.parent.parent),
        "entries": [{"old": str(old), "new": str(new)}],
        "changes": [],
    }


def _state(db):
    with db._lock, db._connect() as sql:
        return {
            name: sql.execute(
                "SELECT * FROM " + name + " ORDER BY 1"
            ).fetchall()
            for name in ("placements", "catalog_entries", "backup_items",
                         "acquisition_ledger", "origin_links", "metadata")
        }


def test_exact_local_only_duplicate_still_merges_catalogue_rows(rig):
    db, old, new, old_id, new_id, artifact, _ = rig
    db.commit_directory_migration(_plan(rig))
    assert db.placement_get(old_id) is None
    assert db.placement_get(new_id)["path"] == str(new)
    assert all(
        db.catalog_entry_get("catalogue:" + name)["placement_id"] == new_id
        for name in ("old", "new")
    )
    assert db.metadata_get(JOURNAL_KEY) is not None


@pytest.mark.parametrize("conflict", [
    "different_artifact", "other_owner", "legacy_source",
    "target_source", "catalogue_source", "missing_source",
    "backup_on_old_path", "backup_on_new_path",
])
def test_collision_cannot_erase_unattested_source_or_change_journal(rig, conflict):
    db, old, new, old_id, new_id, artifact, other = rig
    with db._lock, db._connect() as sql:
        if conflict == "different_artifact":
            sql.execute(
                "UPDATE placements SET artifact_id=? WHERE placement_id=?",
                (other, new_id),
            )
        elif conflict == "other_owner":
            sql.execute(
                "UPDATE placements SET identity_id='bob' WHERE placement_id=?",
                (new_id,),
            )
        elif conflict == "legacy_source":
            sql.execute(
                "UPDATE placements SET provider_id='kobo',"
                "source_item_id='edition-a' WHERE placement_id=?",
                (old_id,),
            )
        elif conflict == "target_source":
            sql.execute(
                "UPDATE placements SET provider_id='kobo',"
                "source_item_id='edition-b' WHERE placement_id=?",
                (new_id,),
            )
        elif conflict == "catalogue_source":
            sql.execute(
                "UPDATE catalog_entries SET provider_id='kobo',"
                "item_id='edition-a' WHERE catalog_entry_id='catalogue:old'"
            )
        elif conflict == "missing_source":
            sql.execute(
                "UPDATE placements SET state='missing' WHERE placement_id=?",
                (old_id,),
            )
        elif conflict.startswith("backup_on_"):
            path = old if conflict == "backup_on_old_path" else new
            sql.execute(
                "INSERT INTO backup_items("
                "provider_id,credential_id,item_id,title,status,updated_at,local_path"
                ") VALUES(?,?,?,?,?,?,?)",
                ("kobo", "vault", "edition-a", "Synthetic", "complete", 1, str(path)),
            )
    before = _state(db)
    with pytest.raises(
        ValueError, match="directory_migration_placement_collision_requires_relink",
    ):
        db.commit_directory_migration(_plan(rig))
    assert _state(db) == before
    assert db.metadata_get(JOURNAL_KEY) is None
    assert db.placement_get(old_id) is not None
    assert db.placement_get(new_id) is not None
