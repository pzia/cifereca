"""#4708: exact read-only archive/Placement reconciliation after restart.

No retry, restore, repair, authority, or filesystem observation. An ambiguous
commit must be inspected, never blindly repeated. No production route.
"""
from __future__ import annotations

import sqlite3

from .missing_placement_archive_sql import _schema, _inventory, _live_row, _TABLE
from .missing_placement_archive_envelope import _decode_archive_row


def review_archive_transition(storage, *, identity_id, expected_payload,
                              expected_digest, max_records, max_bytes,
                              explicitly_requested=False):
    """Classify the exact witness under one read snapshot, never expose it."""
    def report(status):
        return {
            "status": status, "read_only": True,
            "archive_authorized": False, "restore_authorized": False,
            "automatic_retry_allowed": False, "physical_evidence_verified": False,
        }

    if explicitly_requested is not True or type(identity_id) is not str or not identity_id:
        return report("unavailable")
    try:
        row = _decode_archive_row(expected_payload, expected_digest)
        pid, artifact, path, owner = row[:4]
        if owner != identity_id:
            return report("unavailable")
        with storage._lock:
            db = storage._connect()
            # Never commit/rollback a caller's pending work, or describe an
            # uncommitted transition as durable evidence.
            if db.in_transaction:
                return report("unavailable")
            with db:
                db.execute("BEGIN")
                _schema(db)
                records, _ = _inventory(db, max_records=max_records, max_bytes=max_bytes)
                if (db.execute("SELECT 1 FROM main.identities WHERE identity_id=?", (owner,)).fetchone() is None
                        or db.execute("SELECT 1 FROM main.artifacts WHERE artifact_id=?", (artifact,)).fetchone() is None):
                    return report("unavailable")
                live = _live_row(db, pid)
                by_path = db.execute(
                    "SELECT placement_id FROM main.placements WHERE path=? LIMIT 2", (path,)
                ).fetchall()
                archived = records.get(pid)
                archive_paths = db.execute(
                    f"SELECT placement_id FROM main.{_TABLE} WHERE path=? OR digest=? LIMIT 2",
                    (path, expected_digest),
                ).fetchall()
                if (live == row and by_path == [(pid,)] and archived is None
                        and not archive_paths):
                    return report("live_exact_observed")
                if (live is None and not by_path
                        and archived == (row, expected_payload, expected_digest)
                        and archive_paths == [(pid,)]):
                    return report("archive_exact_observed")
                return report("ambiguous_preserve")
    except (sqlite3.Error, OSError, ValueError, TypeError, RuntimeError, RecursionError):
        return report("unavailable")
