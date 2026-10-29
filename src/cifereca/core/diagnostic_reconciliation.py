"""Closed, aggregate-only Core diagnostics for offline library reconciliation.

No title, ISBN, account, file path, SHA, SQLite row or exception text is
accepted. Missing measurements stay absent rather than being treated as zero.
"""
from __future__ import annotations

import re

OPERATION = "library_reconciliation_audit"
_REASONS = frozenset({
    "audit_cancelled", "cancelled", "snapshot_cancelled",
    "metadata_candidate_cancelled", "audit_time_budget", "time_budget",
    "snapshot_time_budget", "hash_byte_budget", "file_byte_budget",
    "entry_budget", "file_budget", "depth_budget", "directory_unavailable",
    "entry_unavailable", "root_changed", "directory_changed", "file_changed",
    "nofollow_unavailable", "epub_parser_limit_or_invalid",
    "structure_limit_or_invalid", "metadata_byte_budget",
    "comparison_budget", "metadata_candidate_budget_or_cancelled",
    "metadata_candidate_time_budget", "metadata_candidate_row_budget",
    "metadata_candidate_reference_budget", "metadata_candidate_keys_budget",
    "metadata_candidate_count_budget", "metadata_unavailable",
    "stored_path_budget", "stored_path_unavailable",
    "snapshot_row_budget", "snapshot_schema_incomplete",
    "snapshot_byte_budget_or_invalid", "snapshot_unavailable",
    "snapshot_changed", "snapshot_reader_unavailable",
    "snapshot_wal_reader_unavailable", "snapshot_identity_migration",
    "reconciliation_scope_changed", "reconciliation_identity_not_active",
    "reconciliation_identity_root_collision",
    "invalid_reconciliation_identity_scope",
    "invalid_reconciliation_root_configuration",
    "unknown_reconciliation_identity", "reconciliation_unavailable",
    "unclassified_limit",
})
_CANCELLED = frozenset({
    "audit_cancelled", "cancelled", "snapshot_cancelled",
    "metadata_candidate_cancelled",
})
_FAILED = frozenset({
    "snapshot_unavailable", "snapshot_reader_unavailable",
    "snapshot_wal_reader_unavailable", "snapshot_identity_migration",
    "reconciliation_scope_changed", "reconciliation_identity_not_active",
    "reconciliation_identity_root_collision",
    "invalid_reconciliation_identity_scope",
    "invalid_reconciliation_root_configuration",
    "unknown_reconciliation_identity", "reconciliation_unavailable",
})
_COUNTS = {
    "files_started": ("inventory", "files_started"),
    "files_observed": ("inventory", "files"),
    "files_hashed": ("inventory", "hashed_files"),
    "entries_examined": ("inventory", "entries"),
    "catalog_rows_examined": ("metadata_candidates", "catalog_rows_examined"),
    "references_examined": ("metadata_candidates", "references_examined"),
    "candidate_files_examined": ("metadata_candidates", "files_examined"),
    "metadata_candidates": ("metadata_candidates", "metadata_candidates"),
}
_PHASES = frozenset({
    "snapshot", "sha256", "epub_opf", "structure", "pdf_signature",
    "candidate_index", "candidate_match", "stored_paths",
})
_INVENTORY_PHASES = frozenset({"sha256", "epub_opf", "structure", "pdf_signature"})
_STATUSES = frozenset({"complete", "partial", "failed", "cancelled"})
_PATH_COUNTS = frozenset({
    "stored_path_present", "stored_path_missing", "stored_path_unavailable",
    "stored_path_out_of_scope",
})
_PATH_CONTEXT = frozenset({
    "sqlite_present", "sqlite_missing", "sqlite_other", "distinct_paths",
    "distinct_artifacts_with_id", "distinct_placements_with_id",
})
_SCOPE_INVALID = frozenset({
    "reconciliation_scope_changed", "reconciliation_identity_not_active",
    "reconciliation_identity_root_collision", "unknown_reconciliation_identity",
    "invalid_reconciliation_identity_scope", "invalid_reconciliation_root_configuration",
})


def _coverage(section: dict) -> str:
    status = section.get("status")
    reasons = section.get("reasons")
    if type(status) is not str:
        return "unverified"
    if status == "complete" and type(reasons) is list and not reasons:
        return "complete"
    if status in {"complete", "incomplete_scan"}:
        return "partial"
    return "unverified"


def _measurements(value: object, keys: frozenset) -> dict:
    return {key: item for key in sorted(keys)
            if type(value) is dict and (item := _number(value.get(key))) is not None}


def _path_summary(report: dict, reasons: set[str]) -> dict:
    """Project measurements already supplied by Core; never probe a path."""
    stored = _section(report, "stored_paths")
    raw_reasons = stored.get("reasons")
    codes = {item if type(item) is str and item in _REASONS else "unclassified_limit"
             for item in raw_reasons} if type(raw_reasons) is list and len(raw_reasons) <= 64 else {"unclassified_limit"}
    coverage = _coverage(stored)
    if codes and coverage == "complete":
        coverage = "partial"
    return {
        "observation_source": "core_stored_paths",
        "coverage": coverage,
        "inventory_coverage": _coverage(_section(report, "inventory")),
        "scope": "invalidated" if (reasons | codes) & _SCOPE_INVALID else "not_supplied",
        "reasons": sorted(codes),
        "counts": _measurements(stored.get("counts"), _PATH_COUNTS),
        "missing_path_context": _measurements(stored.get("missing_path_context"), _PATH_CONTEXT),
    }


def _public_path_summary(value: object) -> dict:
    if (type(value) is not dict or value.get("observation_source") != "core_stored_paths"
            or type(value.get("coverage")) is not str or value["coverage"] not in {"complete", "partial", "unverified"}
            or type(value.get("inventory_coverage")) is not str or value["inventory_coverage"] not in {"complete", "partial", "unverified"}
            or type(value.get("scope")) is not str or value["scope"] not in {"invalidated", "not_supplied"}):
        return {}
    codes = value.get("reasons")
    if (type(codes) is not list or len(codes) > 64
            or any(type(code) is not str or code not in _REASONS for code in codes)
            or value["coverage"] == "complete" and codes):
        return {}
    return {
        "observation_source": "core_stored_paths", "coverage": value["coverage"],
        "inventory_coverage": value["inventory_coverage"], "scope": value["scope"],
        "reasons": sorted(set(codes)),
        "counts": _measurements(value.get("counts"), _PATH_COUNTS),
        "missing_path_context": _measurements(value.get("missing_path_context"), _PATH_CONTEXT),
    }


def known_reason_code(value: object) -> bool:
    """Only Core-defined reconciliation causes are valid public filters."""
    return type(value) is str and value in _REASONS


def _number(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 1_000_000_000_000 else None


def _section(report: dict, name: str) -> dict:
    value = report.get(name)
    return value if type(value) is dict else {}


def from_audit_result(report: object, duration_ms: object = None) -> dict:
    """Make an allowlisted projection of the broker's already-redacted result.

    Incomplete/unknown codes never silently become a complete audit. The
    projection is also checked again at the public event-feed boundary.
    """
    value = report if type(report) is dict else {}
    raw_reasons = value.get("reasons")
    reasons: set[str] = set()
    unknown = type(raw_reasons) is not list or len(raw_reasons) > 64
    if type(raw_reasons) is list and len(raw_reasons) <= 64:
        for reason in raw_reasons:
            if type(reason) is str and reason in _REASONS:
                reasons.add(reason)
            else:
                unknown = True
    if unknown:
        reasons.add("unclassified_limit")
    raw_status = value.get("status")
    safe_status = raw_status if type(raw_status) is str else None
    if safe_status == "complete" and not reasons:
        status = "complete"
    elif reasons & _CANCELLED and not reasons & _FAILED:
        status = "cancelled"
    elif reasons & _FAILED or safe_status not in {"complete", "incomplete_scan"}:
        status = "failed"
    else:
        status = "partial"
    if status != "complete" and not reasons:
        reasons.add("unclassified_limit")
    fields = {
        "schema_version": 1, "operation": OPERATION, "phase": "finished",
        "status": status, "code": sorted(reasons)[0] if reasons else "none",
        "reasons": sorted(reasons),
    }
    elapsed = _number(duration_ms)
    if elapsed is not None:
        fields["duration_ms"] = elapsed
    counts: dict[str, int] = {}
    for target, (section, key) in _COUNTS.items():
        metric = _number(_section(value, section).get(key))
        if metric is not None:
            counts[target] = metric
    fields["counts"] = counts
    times: dict[str, int] = {}
    calls: dict[str, int] = {}
    inventory = _section(value, "inventory")
    raw_times = inventory.get("phase_ms")
    raw_calls = inventory.get("phase_calls")
    if type(raw_times) is dict:
        for phase in _INVENTORY_PHASES:
            measured = _number(raw_times.get(phase))
            if measured is not None:
                times[phase] = measured
    if type(raw_calls) is dict:
        for phase in _INVENTORY_PHASES:
            measured = _number(raw_calls.get(phase))
            if measured is not None:
                calls[phase] = measured
    # Snapshot is measured by the Core broker before the filesystem audit,
    # including the failure path where no inventory result exists.
    measured_snapshot = _number(value.get("snapshot_ms"))
    if measured_snapshot is not None:
        times["snapshot"] = measured_snapshot
    candidates = _section(value, "metadata_candidates")
    for source, target in (("index_ms", "candidate_index"),
                           ("match_ms", "candidate_match")):
        measured = _number(candidates.get(source))
        if measured is not None:
            times[target] = measured
    stored = _number(_section(value, "stored_paths").get("duration_ms"))
    if stored is not None:
        times["stored_paths"] = stored
    fields["phase_ms"] = times
    fields["phase_calls"] = calls
    if "stored_paths" in value:
        fields["stored_paths"] = _path_summary(value, reasons)
    # Core sends this envelope only in the private Diagnostics handoff. No
    # path/identity is reconstructed from the aggregate broker response.
    if "placement_observations" in value and "stored_paths" in fields:
        from .diagnostic_placement_observations import placement_batch
        observations = placement_batch(value["placement_observations"], allowed_reasons=_REASONS)
        if observations is not None and fields["stored_paths"]["scope"] != "invalidated":
            fields["placement_observations"] = observations
    return fields


def public_fields(value: object) -> dict:
    """Revalidate and reconstruct only closed fields for Diagnostics broker."""
    if type(value) is not dict:
        return {}
    if (value.get("schema_version") != 1
            or value.get("operation") != OPERATION
            or value.get("phase") != "finished"
            or type(value.get("status")) is not str
            or value["status"] not in _STATUSES):
        return {}
    reasons = value.get("reasons")
    if (type(reasons) is not list or len(reasons) > 64
            or any(type(reason) is not str or reason not in _REASONS
                   for reason in reasons)):
        return {}
    codes = sorted(set(reasons))
    if value.get("code") != (codes[0] if codes else "none"):
        return {}
    if value["status"] == "complete" and codes:
        return {}
    if value["status"] != "complete" and not codes:
        return {}
    output = {
        "schema_version": 1, "operation": OPERATION, "phase": "finished",
        "status": value["status"], "code": value["code"], "reasons": codes,
    }
    duration = _number(value.get("duration_ms"))
    if duration is not None:
        output["duration_ms"] = duration
    metrics = value.get("counts")
    output["counts"] = {
        key: metric for key in _COUNTS
        if type(metrics) is dict and (metric := _number(metrics.get(key))) is not None
    }
    raw_times = value.get("phase_ms")
    output["phase_ms"] = {
        key: metric for key in _PHASES
        if type(raw_times) is dict and (metric := _number(raw_times.get(key))) is not None
    }
    raw_calls = value.get("phase_calls")
    output["phase_calls"] = {
        key: metric for key in _INVENTORY_PHASES
        if type(raw_calls) is dict and (metric := _number(raw_calls.get(key))) is not None
    }
    if "stored_paths" in value:
        stored = _public_path_summary(value["stored_paths"])
        if not stored:
            return {}
        output["stored_paths"] = stored
    if "placement_observations" in value:
        from .diagnostic_placement_observations import placement_batch
        observations = placement_batch(value["placement_observations"], allowed_reasons=_REASONS)
        if (observations is None or observations != value["placement_observations"]
                or "stored_paths" not in output or output["stored_paths"]["scope"] == "invalidated"):
            return {}
        output["placement_observations"] = observations
    if "task_ref" in value:
        reference = value["task_ref"]
        if type(reference) is not str or re.fullmatch(r"[0-9a-f]{12}", reference) is None:
            return {}
        output["task_ref"] = reference
    return output

# Durable channel for completed offline audits only. Serialize exclusively
# the validated Core projection, never private raw inspection evidence.
_RECONCILIATION_LOG_PREFIX = "EVENT reconciliation audit "
_MAX_RECONCILIATION_LOG = 16384


def public_reconciliation_log_message(value: object) -> str | None:
    import json

    if type(value) is not dict:
        return None
    fields = public_fields(value)
    if not fields or set(fields) != set(value) or fields != value:
        return None
    payload = _RECONCILIATION_LOG_PREFIX + json.dumps(
        fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    return payload if len(payload) <= _MAX_RECONCILIATION_LOG else None


def public_reconciliation_log_fields(body: object) -> dict:
    import json

    if (type(body) is not str
            or len(body) > _MAX_RECONCILIATION_LOG
            or not body.startswith(_RECONCILIATION_LOG_PREFIX)):
        return {}
    try:
        fields = json.loads(body[len(_RECONCILIATION_LOG_PREFIX):])
        canonical = public_reconciliation_log_message(fields)
    except (ValueError, TypeError, RecursionError, OverflowError):
        return {}
    return fields if canonical == body else {}
