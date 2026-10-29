"""Read-only, bounded Diagnostics projection of Core placement observations.

The Core broker attests audit scope/freshness. This module cannot inspect files,
SQLite, or authorize a repair. It preserves only already-attested facts.
"""
from __future__ import annotations

import json
import re

from .diagnostic_acquisition_failure import protect_text

SCHEMA = "cifereca.placement.observation/1"
MAX_SOURCE_ROWS = 256
# Reserve space for the existing reconciliation event (16 KiB journal cap).
MAX_DETAIL_BYTES = 8500
_STATES = frozenset({"present", "missing", "unavailable", "unverified"})
_REASONS = {
    "present": frozenset({"regular_file_observed"}),
    "missing": frozenset({"enoent_observed"}),
    "unavailable": frozenset({
        "probe_unavailable", "invalid_path", "not_regular_file",
        "permission_denied", "symlink_or_not_directory", "io_unavailable",
        "root_changed", "path_changed", "root_missing",
        "root_permission_denied", "root_unavailable",
    }),
    "unverified": frozenset({"out_of_scope", "not_examined"}),
}
_ID_FIELDS = ("placement_id", "artifact_id", "recorded_provider_id",
              "recorded_source_item_id", "sqlite_state")


def _safe_text(value: object, limit: int) -> str | None:
    if (type(value) is not str or not value or len(value) > limit
            or any(ord(ch) < 32 for ch in value)):
        return None
    # Secrets, not ordinary identifiers and paths, are protected. Never
    # silently turn a redacted identity or recorded ID into a valid ID.
    return value if protect_text(value) == value else None


def _row(value: object) -> dict | None:
    if type(value) is not dict:
        return None
    state = value.get("state")
    reason = value.get("reason")
    if state not in _STATES or reason not in _REASONS[state]:
        return None
    if value.get("observation_source") != "core_stored_paths":
        return None
    if value.get("restoration") != "not_evaluated":
        return None
    result = {
        "state": state, "reason": reason,
        "observation_source": "core_stored_paths",
        "restoration": "not_evaluated",
    }
    for key in _ID_FIELDS:
        if key in value:
            text = _safe_text(value[key], 4096 if key == "recorded_source_item_id" else 256)
            if text is None:
                return None
            result[key] = text
    if "path" in value:
        path = value["path"]
        if (type(path) is not str or not path or len(path) > 4096
                or any(ord(ch) < 32 for ch in path)):
            return None
        protected = protect_text(path)
        result["path"] = protected
        if protected != path or value.get("path_credentials_removed") is True:
            result["path_credentials_removed"] = True
    elif "path_credentials_removed" in value:
        return None
    return result


def placement_batch(value: object, *, allowed_reasons: frozenset) -> dict | None:
    """Allowlist and size-limit the *existing* private Core handoff.

    The length counters describe the emitted Diagnostics subset, not a
    fabricated completeness claim. When bounded, skipped rows are never
    reported as missing. Unknown envelopes are omitted, not treated as empty.
    """
    if type(value) is not dict or value.get("schema") != SCHEMA:
        return None
    if (value.get("scope_validated") is not True
            or value.get("read_only") is not True
            or value.get("repair_authorized") is not False
            or value.get("acquisition_authorized") is not False):
        return None
    ref = value.get("observation_ref")
    if type(ref) is not str or re.fullmatch(r"[0-9a-f]{32}", ref) is None:
        return None
    identity = _safe_text(value.get("identity_id"), 256)
    if identity is None:
        return None
    total = value.get("total_rows")
    emitted = value.get("emitted_rows")
    rows = value.get("observations")
    if (type(total) is not int or type(emitted) is not int
            or not 0 <= total <= 1_000_000_000
            or type(rows) is not list or not 0 <= emitted <= MAX_SOURCE_ROWS
            or emitted != len(rows) or emitted > total
            or value.get("truncated") is not (total > emitted)):
        return None
    probe = value.get("probe_coverage")
    inventory = value.get("inventory_coverage")
    if probe not in ("complete", "partial") or inventory not in ("complete", "partial"):
        return None
    reasons = value.get("reasons")
    if (type(reasons) is not list or len(reasons) > 64
            or any(type(x) is not str or x not in allowed_reasons for x in reasons)):
        return None
    if probe == "complete" and reasons:
        return None
    result = {
        "schema": SCHEMA, "observation_ref": ref,
        "identity_id": identity, "scope_validated": True,
        "read_only": True, "repair_authorized": False,
        "acquisition_authorized": False,
        "probe_coverage": probe, "inventory_coverage": inventory,
        "reasons": sorted(set(reasons)),
        "total_rows": total, "emitted_rows": 0, "truncated": total > 0,
        "observations": [],
    }
    for item in rows:
        observation = _row(item)
        if observation is None:
            return None
        candidate = {**result, "emitted_rows": len(result["observations"]) + 1,
                     "observations": [*result["observations"], observation]}
        candidate["truncated"] = total > candidate["emitted_rows"]
        encoded = json.dumps(candidate, ensure_ascii=True, separators=(",", ":"))
        if len(encoded) > MAX_DETAIL_BYTES:
            break
        result = candidate
    return result
