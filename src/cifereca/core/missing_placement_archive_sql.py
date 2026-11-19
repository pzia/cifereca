"""#4708: private archive transaction primitives, not wired to a live command.

The caller owns BEGIN IMMEDIATE/FULL, a fresh exact scope and all dependencies,
the acquisition lock and final revalidation before commit. No schema migration,
commit, filesystem operation or public capacity policy is provided here.
"""
from __future__ import annotations

import sqlite3

from .destructive_cleanup_policy import cleanup_review_epoch, destructive_cleanup_enabled
from .missing_placement_archive_envelope import (
    _COLUMNS, _MAX_BYTES, _decode_archive_row, _encode_archive_row,
)

_TABLE = "missing_placement_archive_v1"
_ARCHIVE_COLUMNS = ("placement_id", "identity_id", "path", "digest", "payload")
# Proposed schema, not installed by Storage. No trigger or cascade.
_DDL = """CREATE TABLE missing_placement_archive_v1 (
    placement_id TEXT PRIMARY KEY NOT NULL,
    identity_id TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    digest TEXT NOT NULL UNIQUE,
    payload BLOB NOT NULL
)"""


class ArchiveRefused(ValueError):
    pass


def _require(condition, reason):
    if condition is not True:
        raise ArchiveRefused(reason)


def _schema(db):
    for table, expected in (("placements", _COLUMNS), (_TABLE, _ARCHIVE_COLUMNS)):
        cols = db.execute(f"PRAGMA main.table_xinfo({table})").fetchall()
        _require(tuple(col[1] for col in cols) == expected
                 and all(col[6] == 0 for col in cols), "archive_schema_changed")
        _require(db.execute(
            "SELECT type FROM main.sqlite_master WHERE name=?", (table,)
        ).fetchone() == ("table",), "archive_schema_changed")
    # No implicit side effects on either target, including temp triggers.
    for schema in ("main", "temp"):
        _require(db.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type='trigger' "
            "AND lower(tbl_name) IN ('placements',?) LIMIT 1", (_TABLE,)
        ).fetchone() is None, "archive_trigger_present")
    # Refuse shadowing and attached databases instead of resolving aliases.
    _require(all(row[1] in ("main", "temp") for row in db.execute("PRAGMA database_list")),
             "archive_attached_database")
    _require(db.execute(
        "SELECT 1 FROM temp.sqlite_master WHERE lower(name) IN ('placements',?) LIMIT 1",
        (_TABLE,)
    ).fetchone() is None, "archive_shadow_table")
    tables = db.execute(
        "SELECT name FROM main.sqlite_master WHERE type='table' LIMIT 513"
    ).fetchall()
    _require(len(tables) <= 512, "archive_schema_budget")
    for (name,) in tables:
        refs = db.execute("SELECT * FROM pragma_foreign_key_list(?, 'main')", (name,)).fetchall()
        _require(all(str(ref[2]).lower() not in ("placements", _TABLE)
                     for ref in refs), "archive_foreign_key_present")


def _admit(db, *, config, expected_epoch, confirmed, scope_current):
    _require(db.in_transaction and db.execute("PRAGMA synchronous").fetchone()[0] >= 2,
             "archive_transaction_required")
    _require(confirmed is True and destructive_cleanup_enabled(config)
             and type(expected_epoch) is str and cleanup_review_epoch(config) == expected_epoch
             and callable(scope_current) and scope_current() is True,
             "archive_scope_or_confirmation_changed")
    _schema(db)


def _inventory(db, *, max_records, max_bytes):
    # Capacity explicit from the caller; hard safety ceilings are not policy.
    _require(type(max_records) is int and 0 < max_records <= 4096
             and type(max_bytes) is int and 0 < max_bytes <= 64 * 1024 * 1024,
             "archive_capacity_unconfigured")
    records = db.execute(
        f"SELECT placement_id,identity_id,path,digest,length(payload) FROM main.{_TABLE} LIMIT ?",
        (max_records + 1,),
    ).fetchall()
    _require(len(records) <= max_records and all(
        type(r[4]) is int and 0 < r[4] <= _MAX_BYTES for r in records
    ), "archive_capacity_or_corruption")
    total = sum(r[4] for r in records)
    _require(total <= max_bytes, "archive_capacity_or_corruption")
    decoded = {}
    for pid, owner, path, digest, _size in records:
        payload, = db.execute(
            f"SELECT payload FROM main.{_TABLE} WHERE placement_id=?", (pid,)
        ).fetchone()
        row = _decode_archive_row(payload, digest)
        _require((row[0], row[3], row[2]) == (pid, owner, path), "archive_corrupt")
        decoded[pid] = (row, payload, digest)
    return decoded, total


def _live_row(db, pid):
    return db.execute(
        "SELECT " + ",".join(_COLUMNS) + " FROM main.placements WHERE placement_id=?",
        (pid,),
    ).fetchone()


def _change(db, *, expected_payload, expected_digest, config, expected_epoch,
            confirmed, scope_current, max_records, max_bytes, restore):
    """One exact row, no commit. Savepoint rolls back partial attempts.

    scope_current must include the caller's live owner/dependency/FS proof.
    It is checked before mutation and after the savepoint's writes; the
    caller still owns the final check and outer transaction commit.
    """
    def current():
        return (destructive_cleanup_enabled(config)
                and cleanup_review_epoch(config) == expected_epoch
                and scope_current() is True)

    _admit(db, config=config, expected_epoch=expected_epoch,
           confirmed=confirmed, scope_current=scope_current)
    row = _decode_archive_row(expected_payload, expected_digest)
    pid, artifact, path, owner = row[:4]
    records, total = _inventory(db, max_records=max_records, max_bytes=max_bytes)
    _require(db.execute("SELECT 1 FROM main.identities WHERE identity_id=?", (owner,)).fetchone()
             is not None and db.execute(
                 "SELECT 1 FROM main.artifacts WHERE artifact_id=?", (artifact,)
             ).fetchone() is not None, "archive_owner_or_artifact_unknown")
    db.execute("SAVEPOINT missing_archive_change")
    try:
        _require(current(), "archive_scope_or_confirmation_changed")
        if restore:
            _require(records.get(pid) == (row, expected_payload, expected_digest),
                     "archive_witness_changed")
            _require(_live_row(db, pid) is None and db.execute(
                "SELECT 1 FROM main.placements WHERE path=?", (path,)
            ).fetchone() is None, "archive_restore_collision")
            db.execute("INSERT INTO main.placements(" + ",".join(_COLUMNS) +
                       ") VALUES(?,?,?,?,?,?,?,?,?,?,?)", row)
            _require(_live_row(db, pid) == row, "archive_restore_postcheck")
            removed = db.execute(
                f"DELETE FROM main.{_TABLE} WHERE placement_id=? AND digest=? AND payload=?",
                (pid, expected_digest, expected_payload),
            )
            _require(removed.rowcount == 1, "archive_witness_changed")
        else:
            _require(_live_row(db, pid) == row, "archive_live_row_changed")
            _require(pid not in records and all(
                path != other[0][2] and expected_digest != other[2]
                for other in records.values()
            ), "archive_collision")
            _require(len(records) < max_records and total + len(expected_payload) <= max_bytes,
                     "archive_capacity_exhausted")
            db.execute(
                f"INSERT INTO main.{_TABLE} VALUES(?,?,?,?,?)",
                (pid, owner, path, expected_digest, expected_payload),
            )
            proof = db.execute(
                f"SELECT identity_id,path,digest,payload FROM main.{_TABLE} WHERE placement_id=?",
                (pid,),
            ).fetchone()
            _require(proof == (owner, path, expected_digest, expected_payload),
                     "archive_insert_postcheck")
            removed = db.execute(
                "DELETE FROM main.placements WHERE placement_id=? AND identity_id=? AND state='missing'",
                (pid, owner),
            )
            _require(removed.rowcount == 1 and _live_row(db, pid) is None,
                     "archive_remove_postcheck")
        _require(current(), "archive_scope_or_confirmation_changed")
        db.execute("RELEASE SAVEPOINT missing_archive_change")
    except BaseException:
        db.execute("ROLLBACK TO SAVEPOINT missing_archive_change")
        db.execute("RELEASE SAVEPOINT missing_archive_change")
        raise


def _reconcile(db, *, expected_payload, expected_digest, max_records, max_bytes):
    """Read-only outcome of an ambiguous commit; never retry a mutation.

    Caller supplies a fresh isolated connection and exact private witness.
    A classification is not a confirmation or a dependency proof.
    """
    if db.in_transaction:
        return "unverified"
    try:
        db.execute("BEGIN")
        try:
            _schema(db)
            row = _decode_archive_row(expected_payload, expected_digest)
            records, _ = _inventory(db, max_records=max_records, max_bytes=max_bytes)
            live = _live_row(db, row[0])
            archived = records.get(row[0])
            collision = db.execute(
                "SELECT 1 FROM main.placements WHERE path=? AND placement_id<>?",
                (row[2], row[0]),
            ).fetchone()
            if collision is not None or any(
                pid != row[0] and other[0][2] == row[2]
                for pid, other in records.items()
            ):
                return "unverified"
            if live == row and archived is None:
                return "live_exact"
            if live is None and archived == (row, expected_payload, expected_digest):
                return "archive_exact"
            return "unverified"
        finally:
            db.rollback()
    except (ValueError, TypeError, RuntimeError, sqlite3.Error):
        return "unverified"
