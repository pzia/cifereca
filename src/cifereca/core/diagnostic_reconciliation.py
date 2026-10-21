"""Closed, aggregate-only Core diagnostics for offline library reconciliation.

No title, ISBN, account, file path, SHA, SQLite row or exception text is
accepted. Missing measurements stay absent rather than being treated as zero.
"""
from __future__ import annotations

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
    "sha256", "epub_opf", "structure", "pdf_signature",
    "candidate_index", "candidate_match",
})
_INVENTORY_PHASES = frozenset({"sha256", "epub_opf", "structure", "pdf_signature"})
_STATUSES = frozenset({"complete", "partial", "failed", "cancelled"})


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
    candidates = _section(value, "metadata_candidates")
    for source, target in (("index_ms", "candidate_index"),
                           ("match_ms", "candidate_match")):
        measured = _number(candidates.get(source))
        if measured is not None:
            times[target] = measured
    fields["phase_ms"] = times
    fields["phase_calls"] = calls
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
    return output
