"""P0 #5069 B9: deterministic read-only maintenance plan *summary*.

Not an apply token. A checksum is diagnostic only; it never grants deletion.
The inventory is deliberately blocked until cross-store references and
updater ownership can be revalidated under the common updater lock.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any

_SCHEMA = "cifereca.updater.maintenance-plan/1"
_STORES = (
    "core_transactions", "plugin_versions", "known_persistent_staging",
    "updater_backups", "core_preactivation_backups",
)
_PUBLIC_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_PENDING = frozenset((*_STORES, "persistent_staging", "cross_store_reference_proofs"))


def retention_maintenance_plan(inventory: dict[str, Any]) -> dict[str, Any]:
    """Produce bounded opaque statistics without dropping safety blockers.

    This function never traverses a filesystem, reads a file or mutates any
    inventory object. The two-stage planner must not be confused with an
    acknowledgement for an eventual GC transaction.
    """
    if (type(inventory) is not dict
            or inventory.get("schema")
            != "cifereca.updater.retention-inventory-combined/1"
            or inventory.get("safe_to_apply") is not False
            or type(inventory.get("plans")) is not dict
            or type(inventory.get("blockers")) is not list
            or type(inventory.get("pending_stores")) is not list):
        raise ValueError("retention_inventory_incomplete")
    plans = inventory["plans"]
    # A new/missing namespace does not silently disappear from the preview.
    if (set(plans) - set(_STORES)
            or not {"core_transactions", "plugin_versions",
                    "known_persistent_staging"}.issubset(plans)):
        raise ValueError("retention_unknown_or_missing_store")
    for values in (inventory["blockers"], inventory["pending_stores"]):
        if any(type(value) is not str or _PUBLIC_CODE.fullmatch(value) is None
               for value in values):
            raise ValueError("retention_invalid_blocker")
    if not set(inventory["pending_stores"]).issubset(_PENDING):
        raise ValueError("retention_unknown_pending_store")
    statistics: dict[str, dict[str, int | bool]] = {}
    for name in _STORES:
        report = plans.get(name)
        if report is None:
            statistics[name] = {
                "known": False, "total": 0, "candidates": 0,
                "retained": 0, "apply_allowed": False,
            }
            continue
        if (type(report) is not dict
                or report.get("schema") != "cifereca.updater.retention-plan/1"
                or report.get("safe_to_apply") is not False
                or type(report.get("total")) is not int
                or type(report.get("candidates")) is not int
                or report["total"] < 0
                or not 0 <= report["candidates"] <= report["total"]
                or type(report.get("objects")) is not list
                or len(report["objects"]) != report["total"]):
            raise ValueError("retention_plan_invalid")
        seen: set[str] = set()
        candidate_rows = 0
        reason_counts = {code: 0 for code in (
            "within_24h", "minimum_generations", "pinned", "active",
            "referenced", "retained_transaction_reference",
        )}
        for row in report["objects"]:
            if (type(row) is not dict
                    or type(row.get("object_id")) is not str
                    or not row["object_id"]
                    or row["object_id"] in seen
                    or row.get("decision") not in {"candidate", "retain"}
                    or type(row.get("reasons")) is not list
                    or any(type(reason) is not str
                           or _PUBLIC_CODE.fullmatch(reason) is None
                           for reason in row["reasons"])):
                raise ValueError("retention_plan_invalid_row")
            seen.add(row["object_id"])
            for code in reason_counts:
                if code in row["reasons"]:
                    reason_counts[code] += 1
            if row["decision"] == "candidate":
                if row["reasons"]:
                    raise ValueError("retention_candidate_with_retention_reason")
                candidate_rows += 1
            elif not row["reasons"]:
                raise ValueError("retention_retained_without_reason")
        if candidate_rows != report["candidates"]:
            raise ValueError("retention_candidate_count_mismatch")
        statistics[name] = {
            "known": True, "total": report["total"],
            "candidates": report["candidates"],
            "retained": report["total"] - report["candidates"],
            "retention_reasons": reason_counts,
            "apply_allowed": False,
        }
    # Hash all original evidence, not just totals; a change in pins,
    # reference graph, or safety blocker must change this diagnostic digest.
    # No API accepts this digest as permission to delete.
    envelope = json.dumps(
        inventory, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "schema": _SCHEMA,
        "mode": "dry_run",
        "safe_to_apply": False,
        "apply_supported": False,
        "plan_sha256": sha256(envelope).hexdigest(),
        "stores": statistics,
        "pending_stores": sorted(set(inventory["pending_stores"])),
        "blockers": sorted(set(inventory["blockers"]) | {
            "cross_store_apply_unimplemented",
        }),
        "next_action": "complete_cross_store_reference_proofs",
    }
