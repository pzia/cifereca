"""#6937: private, bounded capacity observation; never a compaction grant.

The installation quota measures SQLite receipts and declared output sizes.
Only the active profile's receipt-named inodes are observed separately.
Unknown records and detached coordinates are not candidates for deletion.
"""
from __future__ import annotations

from pathlib import Path

from . import derived_variant_journal as journal
from .derived_variant_maintenance_review import MaintenanceReviewRequest
from .derived_variant_recovery_operator import _capture
from .derived_variant_stock_review import review_derived_staging_stock
from .library_sqlite_freshness import sqlite_source_stamp


def _snapshot(scope):
    """One bounded SQL snapshot; private digest witnesses never leave Core."""
    configured = {row["id"]: row["directory_name"] for row in scope.config.identities()}
    with scope.storage._lock:
        db = scope.storage._connect()
        if db.in_transaction:
            raise journal.DerivedJournalConflict("derived_capacity_nested_transaction")
        with db:
            db.execute("BEGIN")
            rows, total = journal._measure(db)
            counts = dict.fromkeys(("pending", "committed", "unreadable", "unbound",
                                    "active_pending", "active_committed",
                                    "other_bound"), 0)
            known_bytes = active_bytes = active_payload = 0
            witnesses = []
            bindings = {}
            for key, size in rows:
                raw = db.execute(
                    "SELECT value FROM metadata WHERE key=?", (key,),
                ).fetchone()[0]
                witnesses.append((key, journal._receipt_digest(raw)))
                phase = "pending" if key.startswith(journal._PREFIX) else "committed"
                counts[phase] += 1
                try:
                    intent = journal._decode(key, raw)
                except journal.DerivedJournalConflict:
                    counts["unreadable"] += 1
                    continue
                known_bytes += intent.output_size
                identity = intent.identity_id
                if identity not in bindings:
                    directory = configured.get(identity)
                    bindings[identity] = (
                        directory if type(directory) is str and directory
                        and directory not in (".", "..")
                        and "/" not in directory and chr(92) not in directory
                        and db.execute(
                            "SELECT directory_name FROM identities WHERE identity_id=? LIMIT 2",
                            (identity,),
                        ).fetchall() == [(directory,)]
                        and db.execute(
                            "SELECT identity_id FROM identities WHERE directory_name=? LIMIT 2",
                            (directory,),
                        ).fetchall() == [(identity,)]
                        else None
                    )
                directory = bindings[identity]
                if directory is None or not all(
                    Path(name).is_relative_to(scope.root / directory)
                    and Path(name) != scope.root / directory
                    for name in (intent.parent_path, intent.staged_path, intent.candidate_path)
                ):
                    counts["unbound"] += 1
                    continue
                if identity == scope.identity_id and directory == scope.directory:
                    counts["active_" + phase] += 1
                    active_bytes += intent.output_size
                    active_payload += size
                else:
                    counts["other_bound"] += 1
            return (tuple(witnesses), tuple(sorted(bindings.items())), counts,
                    total, known_bytes, active_payload, active_bytes)


def read_capacity_review(service, request):
    """Explicit worker-only observation, under the existing exact scope."""
    if (type(request) is not MaintenanceReviewRequest
            or request.service is not service or not request.current()):
        return None
    try:
        scope = _capture(service, request.identity_id)
        if scope is None or not request.current():
            return None
        stamp = sqlite_source_stamp(scope.database)
        if stamp is None:
            return None
        snapshot = _snapshot(scope)
        witnesses, _bindings, counts, payload, known, active_payload, active_bytes = snapshot
        # If any row is corrupt, the stock reviewer may fail closed; do not
        # downgrade that outcome to zero measured physical bytes.
        stock = review_derived_staging_stock(
            scope.storage, library_root=scope.root, identity_id=scope.identity_id,
        )
        if (_snapshot(scope) != snapshot or not scope.current()
                or not request.current() or sqlite_source_stamp(scope.database) != stamp):
            return None
        capacity = {"records": journal._MAX_RECORDS,
                    "payload_bytes": journal._MAX_TOTAL_BYTES,
                    "output_bytes": journal._MAX_RESERVED_OUTPUT_BYTES}
        count = len(witnesses)
        at_limit = (count >= capacity["records"] or payload >= capacity["payload_bytes"]
                    or known >= capacity["output_bytes"])
        saturation = ("at_limit" if at_limit else
                      "unknown" if counts["unreadable"] else "below_limits")
        return {
            "status": "partial" if counts["unreadable"] or counts["unbound"]
                       or stock["status"] != "journal_entries_observed" else "observed",
            "counts": counts, "record_count": count, "payload_bytes": payload,
            "known_output_bytes": known,
            "output_bytes_complete": counts["unreadable"] == 0,
            "active_payload_bytes": active_payload, "active_output_bytes": active_bytes,
            "capacity": capacity, "saturation": saturation,
            "stock": stock,
            "admission_authorized": False, "recovery_authorized": False,
            "quota_refund_authorized": False, "destructive_cleanup_allowed": False,
        }
    except Exception:
        # Includes corrupt/over-budget journal and a revoked or inaccessible
        # source. No zero-valued fallback may claim a free slot.
        return None
