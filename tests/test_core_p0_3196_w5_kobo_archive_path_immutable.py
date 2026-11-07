"""P0 #3196 W5: identity path migration never rewrites Kobo 0.7 history.

Only generic, non-Kobo projection paths retain the legacy relocation policy.
Real schema-v14 SQLite; no filesystem copy, provider I/O or fresh rights.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage
from cifereca.identities.directory_migration import JOURNAL_KEY


@pytest.mark.parametrize("kind", ("change", "entry"))
def test_historical_kobo_locator_stays_immutable_while_other_provider_moves(
    tmp_path, kind,
):
    storage = Storage(tmp_path / "state.sqlite")
    try:
        storage.identity_put({
            "identity_id": "alice", "directory_name": "alice-old",
            "label": "Alice",
        })
        old = tmp_path / "alice-old" / "item.epub"
        new = tmp_path / "alice-new" / "item.epub"
        with storage._lock, storage._connect() as db:
            for provider, card in (("kobo", "kobo-card"), ("paris", "paris-card")):
                db.execute(
                    "INSERT INTO backup_items("
                    "provider_id,credential_id,item_id,title,status,updated_at,"
                    "identity_id,account_id,local_path) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (provider, card, "edition", "Synthetic", "complete",
                     17, "alice", "buyer-a", str(old)),
                )
        plan = {
            "root": str(tmp_path),
            "entries": ([{"old": str(old), "new": str(new)}]
                        if kind == "entry" else []),
            "changes": ([{
                "identity_id": "alice", "old": str(tmp_path / "alice-old"),
                "new": str(tmp_path / "alice-new"),
            }] if kind == "change" else []),
        }
        storage.commit_directory_migration(plan)
        with storage._lock, storage._connect() as db:
            rows = db.execute(
                "SELECT provider_id,local_path,updated_at "
                "FROM backup_items ORDER BY provider_id"
            ).fetchall()
        assert rows[0] == ("kobo", str(old), 17)
        assert rows[1][0] == "paris"
        assert rows[1][1] == str(new)
        assert rows[1][2] >= 17
        assert storage.metadata_get(JOURNAL_KEY) is not None
        assert storage.identity_get("alice")["directory_name"] == (
            "alice-new" if kind == "change" else "alice-old"
        )
    finally:
        storage.close()


def test_preserved_kobo_archive_is_not_borrower_or_b_proof(tmp_path):
    storage = Storage(tmp_path / "state.sqlite")
    try:
        storage.identity_put({
            "identity_id": "alice", "directory_name": "alice-old",
            "label": "Alice",
        })
        old = tmp_path / "alice-old" / "item.epub"
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO backup_items("
                "provider_id,credential_id,item_id,title,status,updated_at,"
                "identity_id,account_id,local_path,sha256,size) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("kobo", "vault", "edition", "Historic", "complete",
                 17, "alice", "buyer-a", str(old), "a" * 64, 123),
            )
            before = {
                table: tuple(db.execute("SELECT * FROM " + table))
                for table in (
                    "entitlements", "acquisition_ledger", "artifacts",
                    "source_references", "origin_links", "placements",
                )
            }
        storage.commit_directory_migration({
            "root": str(tmp_path), "entries": [],
            "changes": [{
                "identity_id": "alice", "old": str(tmp_path / "alice-old"),
                "new": str(tmp_path / "alice-new"),
            }],
        })
        with storage._lock, storage._connect() as db:
            assert db.execute(
                "SELECT local_path,sha256,size,updated_at FROM backup_items "
                "WHERE provider_id='kobo'"
            ).fetchone() == (str(old), "a" * 64, 123, 17)
            for table, original in before.items():
                assert tuple(db.execute("SELECT * FROM " + table)) == original
    finally:
        storage.close()
