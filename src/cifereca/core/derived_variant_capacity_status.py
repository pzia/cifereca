"""#6937: one bounded, immutable read of journal and compact-shadow budgets.

Capacity is *declared reservation*, never measured disk usage or permission
to compact, unlink, refund quota, admit a new transformation or replay.
Both namespaces are validated in the same SQLite read transaction.
"""
from __future__ import annotations

from .derived_variant_journal import (
    _read_logical_receipt,
    DerivedJournalConflict, _PREFIX, _COMMITTED, _MAX_RECORDS,
    _MAX_TOTAL_BYTES, _MAX_RESERVED_OUTPUT_BYTES, _decode, _measure,
)
from .derived_variant_compact_receipt import (
    _MAX_PROOFS, _MAX_PROOF_TOTAL, _measure_compact_in_transaction,
    _retired_receipts, _MAX_RETIREMENT_BYTES,
)


def review_derived_capacity_status(storage, *, identity_id=None):
    """Read-only, bounded, fail-closed global budget plus optional profile use.

    Limits remain installation-wide; a profile's reported usage does not
    grant its own separate quota or assert absence of files on disk.
    """
    if identity_id is not None and (
        type(identity_id) is not str or not 0 < len(identity_id) <= 1024
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in identity_id)
    ):
        raise DerivedJournalConflict("derived_capacity_invalid_profile")
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
            retired = _retired_receipts(db)
            rows, payload_bytes = _measure(db)
            pending = committed = declared_output = 0
            profile_pending = profile_committed = profile_output = 0
            for key, _size in rows:
                raw = _read_logical_receipt(db, key)
                intent = _decode(key, raw)
                declared_output += intent.output_size
                if intent.identity_id == identity_id:
                    profile_output += intent.output_size
                if key.startswith(_PREFIX):
                    pending += 1
                    if intent.identity_id == identity_id:
                        profile_pending += 1
                elif key.startswith(_COMMITTED):
                    committed += 1
                    if intent.identity_id == identity_id:
                        profile_committed += 1
                else:
                    raise DerivedJournalConflict("derived_capacity_unrecognized_receipt")
            if declared_output > _MAX_RESERVED_OUTPUT_BYTES:
                raise DerivedJournalConflict("derived_journal_output_capacity_exceeded")

    return {
        "status": "observed",
        "profile_scoped": identity_id is not None,
        "profile_pending_receipts": profile_pending if identity_id is not None else None,
        "profile_committed_receipts": profile_committed if identity_id is not None else None,
        "profile_declared_output_bytes": profile_output if identity_id is not None else None,
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
        "paired_compact_shadows": shadow_count - len(retired),
        "compact_only_receipts": len(retired),
        "compact_retirement_payload_bytes": 64 * len(retired),
        "compact_retirement_payload_limit": _MAX_RETIREMENT_BYTES,
        "physical_original_payload_bytes": payload_bytes - sum(
            len(raw.encode("ascii")) for raw in retired.values()),
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
