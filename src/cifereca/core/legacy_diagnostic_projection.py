"""Read-only allowlisted projections of historical Kobo backup diagnostics.

This module never invokes a provider, opens SQLite, reads a file or executes
the retired W2 engine. Its only purpose is to redact untrusted historical
diagnostics for CLI and shareable support reports. No A/B rights are inferred.
"""
from __future__ import annotations

import re
from typing import Any


def _safe_kobo_opening_decision(raw: Any) -> dict[str, Any] | None:
    """Strictly filter the durable opening decision before UI/CLI export.

    The database value may have been altered by an old installation or a
    plugin. Do not forward arbitrary keys, messages, identifiers or URLs.
    """
    if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw["schema_version"] != 1:
        return None
    allowed = {
        "disabled", "no_session", "capability_unavailable", "source_disabled",
        "fresh_cache", "started", "partial", "error", "manual_only",
    }
    status = raw.get("status")
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": status if type(status) is str and status in allowed else "unknown",
        "persisted_enabled": raw.get("persisted_enabled") is True,
    }
    for key in ("connected_kobo", "eligible_kobo", "scheduled_kobo"):
        value = raw.get(key)
        if type(value) is int and 0 <= value <= 1_000_000:
            result[key] = value
    checked = raw.get("checked_at")
    if type(checked) is int and 0 <= checked <= 4_102_444_800:
        result["checked_at"] = checked
    return result


def safe_kobo_backup_report(report: dict[str, Any]) -> dict[str, Any]:
    """Create a shareable *persisted* Kobo report without personal data.

    The internal kobo_backup_diagnostic is deliberately rich for the local UI
    and reconciliation tools. Never print that raw dictionary from CLI:
    provider messages can include signed URLs, filenames and bearer values,
    and the backup rows contain private titles, paths and product IDs.
    """
    known_item_statuses = {
        "ok", "complete", "error", "pending", "pending_acquisition",
        "acquiring", "attention_required", "reconcile_required",
        "reconcile_conflict", "archived_book", "skipped_archived", "unlinked_local_books",
        "missing", "remote_owned", "unknown",
    }
    known_source_statuses = {
        "complete", "partial", "failed", "aborted", "cancelled",
        "attention_required", "running", "interrupted", "manifest_fetch",
        "skipped_fresh",
    }
    source_metrics = {
        "position", "total", "updated_at", "failure_streak", "scanned",
        "attempted", "downloaded", "unchanged", "pending",
        "attention_required", "errors", "skipped", "skipped_archived", "remaining",
        "reconcile_pending", "reconcile_skipped", "items_blocked_by_scope",
        "malformed_manifest_items", "duplicate_manifest_items",
        "provider_rejected_manifest_items",
    }

    def count(value: Any) -> int:
        # Boolean is a subclass of int: reject it, as well as untrusted
        # provider strings, floats, negatives and oversized numbers.
        return value if type(value) is int and 0 <= value <= 1_000_000 else 0

    statuses: dict[str, int] = {}
    raw_statuses = report.get("statuses")
    if isinstance(raw_statuses, dict):
        for raw_status, raw_count in raw_statuses.items():
            value = count(raw_count)
            status = (
                raw_status if type(raw_status) is str and raw_status in known_item_statuses
                else "other"
            )
            statuses[status] = min(1_000_000, statuses.get(status, 0) + value)

    sources: list[dict[str, Any]] = []
    raw_sources = report.get("sources")
    if isinstance(raw_sources, list):
        for source in raw_sources[:25]:
            if not isinstance(source, dict):
                continue
            state = source.get("status")
            item: dict[str, Any] = {
                "source_index": len(sources) + 1,
                "status": (
                    state if type(state) is str and state in known_source_statuses
                    else "unknown"
                ),
            }
            source_id = source.get("source_id")
            # The checkpoint key contains a 24-character SHA-256 digest, not
            # a raw Kobo credential, identity, product ID or account name.
            if isinstance(source_id, str) and re.fullmatch(r"[0-9a-f]{24}", source_id):
                item["source_id"] = source_id
            for key in source_metrics:
                value = source.get(key)
                # A Unix timestamp exceeds 1 million seconds, unlike normal
                # item counters. Reject future/invalid dates independently.
                limit = (
                    4_102_444_800 if key == "updated_at" else
                    6 if key == "failure_streak" else 1_000_000
                )
                if type(value) is int and 0 <= value <= limit:
                    item[key] = value
            sources.append(item)

    return {
        "redacted": True,
        "items": count(report.get("items")),
        "items_blocked_by_scope": count(report.get("items_blocked_by_scope")),
        "statuses": statuses,
        "error_count": statuses.get("error", 0),
        "reconcile_count": (
            statuses.get("reconcile_required", 0)
            + statuses.get("reconcile_conflict", 0)
        ),
        "attention_count": statuses.get("attention_required", 0),
        "opening_decision": _safe_kobo_opening_decision(report.get("opening_decision")),
        "sources": sources,
    }


_KOBO_DIAGNOSTIC_METRICS = frozenset({
    "sessions", "manifests_fetched", "manifest_items", "scanned", "attempted",
    "downloaded", "unchanged", "completed", "pending", "attention_required",
    "errors", "remaining", "skipped", "skipped_archived", "metadata_refreshed",
    "malformed_manifest_items", "duplicate_manifest_items",
    "provider_rejected_manifest_items", "reconcile_pending", "reconcile_conflicts", "reconcile_skipped",
    "items_blocked_by_scope", "blocked_non_retryable", "attempts_saved",
    "recovered_interrupted", "recovered_unparsed", "legacy_pruned",
    "legacy_local_preserved", "legacy_entitlements_inactivated",
    "vault_errors", "adept_errors", "integration_errors", "cancelled_sources",
})
_KOBO_DIAGNOSTIC_STATUSES = frozenset({
    "complete", "partial", "failed", "aborted", "cancelled",
    "attention_required", "skipped_fresh",
})


def _safe_kobo_backup_diagnostic_detail(summary: dict[str, Any]) -> dict[str, Any]:
    """Export only stable, non-identifying batch metrics.

    Diagnostics.text() may be shared externally. Its generic redactor cannot
    distinguish a Kobo book title, local path, identity ID or raw credential.
    Preserve the full summary *only* for the in-process caller/progress API.
    """
    def metric_fields(record: dict[str, Any]) -> dict[str, int]:
        return {
            key: value for key, value in record.items()
            if key in _KOBO_DIAGNOSTIC_METRICS
            and type(value) is int and 0 <= value <= 1_000_000
        }

    def safe_state(raw: Any) -> str:
        value = str(raw or "")
        return value if value in _KOBO_DIAGNOSTIC_STATUSES else "unknown"

    def safe_error(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        # Even error identifiers are untrusted plugin strings. Do not export
        # arbitrary messages, exception text, URLs or credential fragments.
        known_codes = {
            "unauthorized", "missing_token", "invalid_token", "invalid_bearer",
            "forbidden", "insufficient_scope", "invalid_api_token",
            "provider_http_error", "missing_delegation_scope",
            "no_compatible_http_candidate", "no_downloadable_artifact",
            "missing_pinned_delivery_capability", "adept_error",
            "result_unparseable_artifact_present", "archived_book",
            "network_error", "timeout", "rate_limited", "other",
        }
        raw_phase = str(raw.get("phase") or "")
        raw_code = str(raw.get("code") or "")
        record: dict[str, Any] = {
            "phase": raw_phase if raw_phase in {"vault", "adept", "integration"} else "other",
            "code": raw_code if raw_code in known_codes else "other",
        }
        status = raw.get("status_code")
        if type(status) is int and 100 <= status <= 599:
            record["status_code"] = status
        count = raw.get("count")
        if type(count) is int and 0 <= count <= 1_000_000:
            record["count"] = count
        return record

    result: dict[str, Any] = {
        "scope": "account" if summary.get("scope") == "account" else "all",
        "status": safe_state(summary.get("status")),
        "aborted": summary.get("aborted") is True,
        "cancelled": summary.get("cancelled") is True,
        **metric_fields(summary),
    }
    source_summaries = []
    if isinstance(summary.get("sources"), list):
        for index, source in enumerate(summary["sources"][:25], start=1):
            if not isinstance(source, dict):
                continue
            source_summaries.append({
                "source_index": index,
                "status": safe_state(source.get("status")),
                **metric_fields(source),
            })
    result["sources"] = source_summaries
    errors = summary.get("error_summary")
    result["error_summary"] = [
        parsed for raw in (errors[:10] if isinstance(errors, list) else [])
        if (parsed := safe_error(raw)) is not None
    ]
    abort = safe_error(summary.get("abort_reason"))
    if abort is not None:
        result["abort_reason"] = abort
    return result
