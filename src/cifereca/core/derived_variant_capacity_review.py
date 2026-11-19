"""#6937: operational capacity and observed staging stock, never retirement.

Aggregate installation-wide counters and active owner counters from the
SAME bounded SQL snapshot. Physical observation is independent and limited
to named receipts, not a scan of all staging directories or an archive.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

from .derived_variant_journal import (
    DerivedJournalConflict, _measure, _decode, _receipt_digest, _PREFIX,
    _MAX_RECORDS, _MAX_TOTAL_BYTES, _MAX_RESERVED_OUTPUT_BYTES, _MAX_RECORD_BYTES,
)
from .derived_variant_retention_review import _directory
from .derived_variant_recovery_operator import _capture
from .derived_variant_stock_review import review_derived_staging_stock

_LIMITS = {"records": _MAX_RECORDS, "payload_bytes": _MAX_TOTAL_BYTES,
           "reserved_output_bytes": _MAX_RESERVED_OUTPUT_BYTES}


def _empty():
    return {"records": 0, "pending": 0, "committed": 0, "payload_bytes": 0,
            "reserved_output_bytes": 0}


def _add(counts, key, length, intent):
    counts["records"] += 1
    counts["pending" if key.startswith(_PREFIX) else "committed"] += 1
    counts["payload_bytes"] += length
    counts["reserved_output_bytes"] += intent.output_size


def _snapshot(scope):
    with scope.storage._lock:
        db = scope.storage._connect()
        if db.in_transaction:
            raise DerivedJournalConflict("derived_capacity_nested_snapshot")
        with db:
            db.execute("BEGIN")
            if not scope.current(db):
                raise DerivedJournalConflict("derived_capacity_scope_changed")
            rows, total = _measure(db)
            all_counts, owner, unbound = _empty(), _empty(), _empty()
            directories = {}
            witness = []
            for key, length in rows:
                raw = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0]
                intent = _decode(key, raw)
                witness.append((key, _receipt_digest(raw)))
                _add(all_counts, key, length, intent)
                if intent.identity_id == scope.identity_id:
                    _add(owner, key, length, intent)
                if intent.identity_id not in directories:
                    try:
                        directories[intent.identity_id] = _directory(db, intent.identity_id)
                    except DerivedJournalConflict:
                        directories[intent.identity_id] = None
                directory = directories[intent.identity_id]
                profile = scope.root / directory if directory is not None else None
                if (profile is None or any(
                        Path(raw_path) == profile or not Path(raw_path).is_relative_to(profile)
                        for raw_path in (intent.parent_path, intent.staged_path, intent.candidate_path))):
                    # Unbound receipts are STILL charged; orphaned ownership
                    # or legacy scope is never an eviction recommendation.
                    _add(unbound, key, length, intent)
            if total != all_counts["payload_bytes"]:
                raise DerivedJournalConflict("derived_capacity_inconsistent")
            # Include directory bindings to invalidate a silent owner edit.
            return (tuple(witness), tuple(sorted(directories.items())),
                    all_counts, owner, unbound)


def _report(status, *, installation=None, profile=None, unbound=None,
            remaining=None, stock=None):
    return {
        "status": status, "limits": dict(_LIMITS),
        "max_receipt_payload_bytes": _MAX_RECORD_BYTES,
        "installation": installation, "profile": profile,
        "scope_unverified_receipts": unbound,
        "remaining_operational": remaining,
        "profile_named_stock": stock,
        "per_profile_quota": False,
        "physical_installation_usage_known": False,
        "untracked_staging_examined": False,
        "archive_budget_defined": False,
        "reclaimable_bytes_known": False,
        "reservation_authorized": False,
        "quota_refund_authorized": False,
        "retention_authorized": False,
        "destructive_cleanup_allowed": False,
    }


def review_derived_capacity(service, request):
    """Explicit operator worker. Return unknown rather than false zero."""
    try:
        from .derived_variant_maintenance_review import MaintenanceReviewRequest
        if (type(request) is not MaintenanceReviewRequest or request.service is not service
                or not request.current()):
            return _report("unavailable")
        scope = _capture(service, request.identity_id)
        if scope is None or not request.current():
            return _report("unavailable")
        observed = _snapshot(scope)
        _, _, installation, owner, unbound = observed
        remaining = {key: max(0, limit - installation[key])
                     for key, limit in _LIMITS.items()}
        status = ("over_capacity" if any(installation[key] > limit
                                        for key, limit in _LIMITS.items())
                  else "limit_reached" if not all(remaining.values())
                  else "observed")
        stock = review_derived_staging_stock(
            scope.storage, library_root=scope.root, identity_id=scope.identity_id,
        )
        # Guard against a concurrent reservation/commit and profile ABA.
        if (not request.current() or not scope.current()
                or _snapshot(scope) != observed or not scope.current()
                or not request.current()):
            return _report("evidence_changed")
        return _report(status, installation=installation, profile=owner,
                       unbound=unbound, remaining=remaining, stock=stock)
    except (DerivedJournalConflict, sqlite3.Error, OSError, ValueError, TypeError,
            AttributeError, RuntimeError, RecursionError):
        return _report("unavailable")
