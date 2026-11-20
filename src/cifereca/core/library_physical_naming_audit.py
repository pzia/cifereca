"""#6935: bounded physical naming audit, aggregate public contract only.

Use the same exact per-placement read as Organiser. No keeper selection,
provider, GET, scan/write, plan execution, or filename inference from C.
A missing SQL state is not a fresh absence proof. An incomplete or revoked
snapshot is never reported as a completely compliant library.
"""
from __future__ import annotations

from time import monotonic

from .canonical_placement_inventory import read_canonical_placement_inventory
from .library_organizer_review import (
    admit_organizer_review, _placement_facts, review_organizer_file, _MAX_COPIES,
)
from .library_sqlite_freshness import sqlite_source_stamp

_MAX_BYTES = 2 * 1024 ** 3
_MAX_FILE_BYTES = 128 * 1024 ** 2
_SECONDS = 120
_COUNTS = ("conforming", "deviating", "missing", "unavailable", "ambiguous", "unexamined")


def unavailable_naming_audit():
    return {"physical_schema": 1, "status": "unavailable", "total": 0,
            **dict.fromkeys(_COUNTS, 0)}


def audit_physical_names(service, *, identity_id, control=None):
    """Exact active identity; no private coordinates leave the Core broker."""
    cancelled = False

    def check():
        nonlocal cancelled
        if control is not None:
            try:
                control.check_cancelled()
            except Exception:
                cancelled = True
                raise

    def progress(current, total):
        check()
        if control is not None:
            control.report(current=current, total=total,
                           message="Noms : vérification physique des placements")

    try:
        check()
        deadline = monotonic() + _SECONDS
        scope = admit_organizer_review(service)
        if (type(identity_id) is not str or scope is None
                or scope.identity != identity_id or not scope.bound()):
            return unavailable_naming_audit()
        stamp = sqlite_source_stamp(scope.database)
        if stamp is None:
            return unavailable_naming_audit()
        inventory = read_canonical_placement_inventory(scope.storage, identity_id=identity_id)
        if inventory.status not in ("reviewed", "partial"):
            return unavailable_naming_audit()
        total = inventory.placement_count
        counts = dict.fromkeys(_COUNTS, 0)
        remaining = _MAX_BYTES
        progress(0, total)
        for group in inventory.groups:
            check()
            if not scope.bound():
                return unavailable_naming_audit()
            number = len(group.placements)
            if monotonic() >= deadline:
                counts["unexamined"] += number
                continue
            if (not group.artifact_id or len(group.placements) > _MAX_COPIES
                    or group.reason in ("placement_or_artifact_unverified", "catalogue_scope_mismatch")):
                counts["ambiguous"] += number
                progress(sum(counts.values()), total)
                continue
            snapshot = _placement_facts(scope, group)
            if snapshot is None:
                # A read inconsistency invalidates the whole result.
                return unavailable_naming_audit()
            rows, descriptions = snapshot
            for row in rows:
                check()
                if monotonic() >= deadline:
                    counts["unexamined"] += 1
                    continue
                size = row[4]
                if row[2] == "present":
                    if type(size) is not int or size <= 0:
                        counts["ambiguous"] += 1
                        continue
                    if size > _MAX_FILE_BYTES or size > remaining:
                        counts["unexamined"] += 1
                        continue
                    remaining -= size
                observation, _verified_path = review_organizer_file(
                    scope, group.artifact_id, row, descriptions[row[0]],
                )
                reason = observation["reason"]
                if reason == "name_unchanged":
                    category = "conforming"
                elif reason in ("rename_advice_only", "destination_occupied"):
                    category = "deviating"
                elif reason == "registered_missing":
                    category = "missing"
                elif reason in ("description_unverified", "receipt_discriminator_preserve",
                                "naming_policy_unverified"):
                    category = "ambiguous"
                else:
                    category = "unavailable"
                counts[category] += 1
                progress(sum(counts.values()), total)
            if _placement_facts(scope, group) != snapshot:
                return unavailable_naming_audit()
        check()
        if (sum(counts.values()) != total or not scope.bound()
                or sqlite_source_stamp(scope.database) != stamp):
            return unavailable_naming_audit()
        progress(total, total)
        if not scope.bound() or sqlite_source_stamp(scope.database) != stamp:
            return unavailable_naming_audit()
        return {"physical_schema": 1,
                "status": "complete" if counts["conforming"] + counts["deviating"] == total else "partial",
                "total": total, **counts}
    except Exception:
        # Cancellation must propagate through the task runner instead of
        # being converted into a plausible empty audit.
        if cancelled:
            raise
        check()
        return unavailable_naming_audit()
