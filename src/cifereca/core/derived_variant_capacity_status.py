"""#6937: one bounded, immutable read of journal and compact-shadow budgets.

Capacity is *declared reservation*, never measured disk usage or permission
to compact, unlink, refund quota, admit a new transformation or replay.
Both namespaces are validated in the same SQLite read transaction.
"""
from __future__ import annotations

from .derived_variant_journal import (
    DerivedJournalConflict, _PREFIX, _COMMITTED, _MAX_RECORDS,
    _MAX_TOTAL_BYTES, _MAX_RESERVED_OUTPUT_BYTES, _decode, _measure,
)
from .derived_variant_compact_receipt import (
    _MAX_PROOFS, _MAX_PROOF_TOTAL, _measure_compact_in_transaction,
)


def review_derived_capacity_status(storage):
    """Read-only, bounded, fail-closed status for all recorded profiles."""
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise DerivedJournalConflict("derived_capacity_nested_transaction")
        with db:
            db.execute("BEGIN")
            # An orphan or corrupt shadow is not an available quota slot.
            shadow_count, shadow_bytes = _measure_compact_in_transaction(
                db, require_paired=True,
            )
            rows, payload_bytes = _measure(db)
            pending = committed = declared_output = 0
            for key, _size in rows:
                raw = db.execute(
                    "SELECT value FROM metadata WHERE key=?", (key,),
                ).fetchone()[0]
                intent = _decode(key, raw)
                declared_output += intent.output_size
                if key.startswith(_PREFIX):
                    pending += 1
                elif key.startswith(_COMMITTED):
                    committed += 1
                else:
                    raise DerivedJournalConflict("derived_capacity_unrecognized_receipt")
            if declared_output > _MAX_RESERVED_OUTPUT_BYTES:
                raise DerivedJournalConflict("derived_journal_output_capacity_exceeded")

    return {
        "status": "observed",
        "pending_receipts": pending,
        "committed_receipts": committed,
        "receipt_count": len(rows),
        "receipt_count_limit": _MAX_RECORDS,
        "receipt_count_remaining": _MAX_RECORDS - len(rows),
        "receipt_payload_bytes": payload_bytes,
        "receipt_payload_limit": _MAX_TOTAL_BYTES,
        "receipt_payload_remaining": _MAX_TOTAL_BYTES - payload_bytes,
        "declared_output_bytes": declared_output,
        "declared_output_limit": _MAX_RESERVED_OUTPUT_BYTES,
        "declared_output_remaining": _MAX_RESERVED_OUTPUT_BYTES - declared_output,
        "paired_compact_shadows": shadow_count,
        "compact_shadow_payload_bytes": shadow_bytes,
        "compact_shadow_limit": _MAX_PROOFS,
        "compact_shadow_payload_limit": _MAX_PROOF_TOTAL,
        "saturation_observed": (
            len(rows) >= _MAX_RECORDS
            or payload_bytes >= _MAX_TOTAL_BYTES
            or declared_output >= _MAX_RESERVED_OUTPUT_BYTES
        ),
        "physical_disk_usage_measured": False,
        "full_filesystem_inventory": False,
        "new_transform_admitted": False,
        "quota_refund_authorized": False,
        "retention_authorized": False,
        "destructive_cleanup_allowed": False,
    }
