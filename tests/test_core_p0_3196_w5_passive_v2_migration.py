"""#3196 W5: old SQLite v2 Kobo archives do not become rights in v3.

Physical files and provider sessions are intentionally absent. The v2->v3
migration is an offline import, not a fresh Kobo-owned-right observation.
"""
from __future__ import annotations

import sqlite3

import pytest

from cifereca.storage import Storage


_SCHEMA_V2 = """
CREATE TABLE cache(namespace TEXT NOT NULL,cache_key TEXT NOT NULL,payload TEXT NOT NULL,
  stored_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,PRIMARY KEY(namespace,cache_key));
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT INTO metadata(key,value) VALUES('schema_version','2');
CREATE TABLE backup_items(
  provider_id TEXT NOT NULL, credential_id TEXT NOT NULL, item_id TEXT NOT NULL,
  revision_id TEXT, title TEXT NOT NULL, author TEXT, format TEXT, local_path TEXT,
  status TEXT NOT NULL, size INTEGER, sha256 TEXT, updated_at INTEGER NOT NULL,
  error TEXT, job_id TEXT, account_id TEXT, entitlement_type TEXT NOT NULL DEFAULT 'owned',
  loan_id TEXT, expires_at TEXT, acquisition_strategy TEXT, executor TEXT,
  adept_profile TEXT, attempt_count INTEGER NOT NULL DEFAULT 0,
  last_attempt_at INTEGER, attention_code TEXT,
  PRIMARY KEY(provider_id,credential_id,item_id)
);
"""


def _v2(path, items):
    with sqlite3.connect(path) as db:
        db.executescript(_SCHEMA_V2)
        for item in items:
            row = {
                "provider_id": "kobo", "credential_id": "vault",
                "account_id": "buyer", "item_id": "edition",
                "revision_id": "old", "title": "Historical", "status": "pending",
                "updated_at": 1700000000, "entitlement_type": "owned",
                "attempt_count": 0,
                **item,
            }
            columns = list(row)
            db.execute(
                "INSERT INTO backup_items(" + ",".join(columns) + ") "
                "VALUES(" + ",".join("?" for _ in columns) + ")",
                tuple(row.values()),
            )


def _positive(store):
    return {
        table: [
            (row["provider_id"], row["credential_id"], row["item_id"])
            for row in getattr(store, reader)()
        ]
        for table, reader in (
            ("rights", "entitlements"),
            ("receipts", "acquisitions"),
        )
    }


def test_v2_passive_kobo_backups_survive_without_synthesizing_v3_rights(tmp_path):
    db_path = tmp_path / "old-reader.sqlite"
    rows = [
        {"item_id": "archived", "status": "skipped_archived",
         "local_path": "/historical-do-not-open.epub",
         "sha256": "a" * 64, "size": 111},
        {"item_id": "pending", "status": "pending"},
        {"item_id": "queued", "status": "queued"},
        # A prior acquisition attempt is not *passive*. Keep the legacy
        # compatibility ledger until W2 supervision can replace it.
        {"item_id": "interrupted", "status": "pending",
         "attempt_count": 1, "last_attempt_at": 1700000000},
        {"item_id": "complete", "status": "complete",
         "local_path": "/historical-complete.epub", "sha256": "b" * 64,
         "size": 42, "attempt_count": 1},
        {"provider_id": "external", "credential_id": "other",
         "account_id": "other-buyer", "item_id": "other-provider",
         "status": "skipped_archived"},
    ]
    _v2(db_path, rows)
    with sqlite3.connect(db_path) as db:
        before = db.execute(
            "SELECT provider_id,credential_id,item_id,status,local_path,sha256,size,"
            "attempt_count,last_attempt_at FROM backup_items ORDER BY 1,2,3"
        ).fetchall()

    storage = Storage(db_path)
    try:
        assert storage.schema_version() == 14
        with storage._lock, storage._connect() as db:
            assert db.execute(
                "SELECT provider_id,credential_id,item_id,status,local_path,sha256,size,"
                "attempt_count,last_attempt_at FROM backup_items ORDER BY 1,2,3"
            ).fetchall() == before
            # Offline migration is not permitted to mint typed rights.
            assert db.execute("SELECT COUNT(*) FROM source_references").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM artifacts WHERE sha256=?", ("a" * 64,)).fetchone()[0] == 0
            assert db.execute(
                "SELECT COUNT(*) FROM placements WHERE path=?",
                ("/historical-do-not-open.epub",),
            ).fetchone()[0] == 0
            assert db.execute(
                "SELECT COUNT(*) FROM catalog_entries WHERE item_id='archived'"
            ).fetchone()[0] == 0
        facts = _positive(storage)
        for table in ("rights", "receipts"):
            selected = set(facts[table])
            for passive in ("archived", "pending", "queued"):
                assert ("kobo", "vault", passive) not in selected
            assert ("kobo", "vault", "complete") in selected
            assert ("kobo", "vault", "interrupted") in selected
            assert ("external", "other", "other-provider") in selected
    finally:
        storage.close()

    # Idempotent forward-only migration; no archive gets deleted on reopen.
    reopened = Storage(db_path)
    try:
        assert reopened.schema_version() == 14
        assert _positive(reopened) == facts
        assert {row["item_id"] for row in reopened.backup_list("kobo")} == {
            "archived", "pending", "queued", "complete", "interrupted",
        }
    finally:
        reopened.close()


@pytest.mark.parametrize("clue", [
    {"attempt_count": 1},
    {"last_attempt_at": 1700000000},
    {"local_path": "/older-file.epub"},
    {"sha256": "a" * 64},
    {"size": 13},
    {"error": "interrupted while writing"},
    {"attention_code": "interrupted_acquisition_review"},
    {"acquisition_strategy": "acsm-descriptor"},
    {"executor": "kobo-adept"},
])
def test_v2_pending_with_interruption_clue_remains_reviewable(tmp_path, clue):
    db_path = tmp_path / "partial.sqlite"
    _v2(db_path, [{"status": "pending", **clue}])
    storage = Storage(db_path)
    try:
        assert storage.schema_version() == 14
        assert storage.backup_get("kobo", "vault", "edition") is not None
        assert (
            "kobo", "vault", "edition"
        ) in set(_positive(storage)["receipts"])
        with storage._lock, storage._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM source_references").fetchone()[0] == 0
    finally:
        storage.close()
