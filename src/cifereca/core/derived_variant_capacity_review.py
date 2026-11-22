"""#6937: private, bounded capacity observation; never a compaction grant.

The installation quota measures SQLite receipts and declared output sizes.
Only the active profile's receipt-named inodes are observed separately.
Unknown records and detached coordinates are not candidates for deletion.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from unicodedata import category

from . import derived_variant_journal as journal
from . import derived_variant_compact_receipt as compact
from .derived_variant_maintenance_review import MaintenanceReviewRequest
from .derived_variant_recovery_operator import _capture
from .derived_variant_stock_review import review_derived_staging_stock
from .derived_variant_region_review import review_derived_private_region
from .library_sqlite_freshness import sqlite_source_stamp


def _valid_profile_directory(value):
    # Capacity cannot recognize a profile owner more permissively than the
    # exact identity-directory binding used by private retirement reviews.
    return (type(value) is str and 0 < len(value) <= 256
            and value not in (".", "..") and "/" not in value
            and chr(92) not in value
            and not any(category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                        for char in value))


def _receipt_witness(raw):
    """Snapshot invalid native SQLite values without treating them as receipts.

    The journal decoder must still reject non-TEXT records. Their typed
    digest only allows a stable, redacted partial capacity observation.
    """
    if type(raw) is str:
        try:
            return ("text", journal._receipt_digest(raw))
        except UnicodeError:
            raise journal.DerivedJournalConflict("derived_capacity_receipt_unverified") from None
    if type(raw) is bytes:
        return ("blob", hashlib.sha256(raw).hexdigest())
    if type(raw) in (int, float):
        return (type(raw).__name__, hashlib.sha256(str(raw).encode("ascii")).hexdigest())
    raise journal.DerivedJournalConflict("derived_capacity_receipt_unverified")


def _compact_snapshot(db):
    """Independent capacity and exact shadow/original comparison.

    An unpaired compact value is NOT an authoritative retired receipt.
    Unknown or over-budget stores don't hide the ordinary journal figures.
    """
    limits = {"records": compact._MAX_PROOFS, "payload_bytes": compact._MAX_PROOF_TOTAL}
    try:
        count, total = compact._measure_compact_in_transaction(db)
        retired = compact._retired_receipts(db)
        rows = db.execute(
            "SELECT key,value FROM metadata WHERE key>=? AND key<? ORDER BY key LIMIT ?",
            (compact._PREFIX, compact._END, compact._MAX_PROOFS + 1),
        ).fetchall()
        matched = 0
        witnesses = []
        for key, raw in rows:
            original_key, original = compact.decode_committed_witness(key, raw)
            existing = db.execute(
                "SELECT CASE WHEN length(CAST(value AS BLOB)) BETWEEN 1 AND ? "
                "THEN value ELSE NULL END FROM metadata WHERE key=?",
                (journal._MAX_RECORD_BYTES, original_key),
            ).fetchone()
            compact_only = retired.get(original_key) == original
            if existing == (original,) or compact_only:
                matched += 1
            witnesses.append((key, journal._receipt_digest(raw), compact_only))
        if len(rows) != count:
            raise journal.DerivedJournalConflict("derived_compact_snapshot_changed")
        return tuple(witnesses), {
            "status": "observed" if matched == count else "partial",
            "count": count, "payload_bytes": total, "matched": matched,
            "unpaired": count - matched, "retired": len(retired), "capacity": limits,
            "saturation": ("at_limit" if count >= limits["records"]
                           or total >= limits["payload_bytes"] else "below_limits"),
            "retirement_authorized": False, "quota_refund_authorized": False,
        }
    except journal.DerivedJournalConflict:
        return (), {"status": "unavailable", "count": None,
                    "payload_bytes": None, "matched": None, "unpaired": None, "retired": None,
                    "capacity": limits, "saturation": "unknown",
                    "retirement_authorized": False, "quota_refund_authorized": False}


def _snapshot(scope):
    """One bounded SQL snapshot; private digest witnesses never leave Core."""
    # Config may contain duplicate identity rows even when SQLite has one
    # exact owner. A dict comprehension would silently accept the last
    # directory as physical ownership evidence; mark that identity unbound.
    rows = scope.config.identities()
    # The configured-owner inventory is a bounded, native snapshot. A
    # generator, truncated list or malformed row is not ownership evidence.
    if type(rows) is not list or not 1 <= len(rows) <= 128:
        raise journal.DerivedJournalConflict("derived_capacity_config_unverified")
    configured = {}
    for row in rows:
        if (type(row) is not dict or type(row.get("id")) is not str
                or not 0 < len(row["id"]) <= 1024
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in row["id"])
                or "directory_name" not in row):
            raise journal.DerivedJournalConflict("derived_capacity_config_unverified")
        identity = row["id"]
        configured[identity] = (
            row["directory_name"] if identity not in configured else None
        )
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
                raw = journal._read_logical_receipt(db, key)
                witnesses.append((key, _receipt_witness(raw)))
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
                        directory if _valid_profile_directory(directory)
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
                    total, known_bytes, active_payload, active_bytes, _compact_snapshot(db))


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
        witnesses, _bindings, counts, payload, known, active_payload, active_bytes, compact_snapshot = snapshot
        _compact_witnesses, compact_report = compact_snapshot
        # If any row is corrupt, the stock reviewer may fail closed; do not
        # downgrade that outcome to zero measured physical bytes.
        stock = review_derived_staging_stock(
            scope.storage, library_root=scope.root, identity_id=scope.identity_id,
        )
        region = review_derived_private_region(
            scope.storage, library_root=scope.root, identity_id=scope.identity_id,
            explicitly_requested=True,
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
                       or stock["status"] != "journal_entries_observed"
                       or region["status"] != "observed"
                       or compact_report["status"] != "observed" else "observed",
            "counts": counts, "record_count": count, "payload_bytes": payload,
            "known_output_bytes": known,
            "output_bytes_complete": counts["unreadable"] == 0,
            "active_payload_bytes": active_payload, "active_output_bytes": active_bytes,
            "capacity": capacity, "saturation": saturation,
            "stock": stock, "region": region, "compact": compact_report,
            "admission_authorized": False, "recovery_authorized": False,
            "quota_refund_authorized": False, "destructive_cleanup_allowed": False,
        }
    except Exception:
        # Includes corrupt/over-budget journal and a revoked or inaccessible
        # source. No zero-valued fallback may claim a free slot.
        return None
