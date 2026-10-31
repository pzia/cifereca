"""P0 #3882: private, bounded, atomic *safe* sync report ZIP export.

This is NOT the raw HTTP trace and never opens the global journal. Callers
supply two Core-produced scoped audits and opt-in closed correlated events.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from ..startup_revision import build_identity
from .sync_cli_contract import safe_sync_result

_MAX_BYTES = 8 * 1024 * 1024
_MAX_ROWS = 10_000
_REF = re.compile(r"[0-9a-f]{24}")
_PROVIDER = re.compile(r"[a-z][a-z0-9._-]{0,63}")
_CODES = {
    "complete_local", "staged_unpublished", "published_unverified",
    "receipt_without_placement", "placement_without_origin", "missing_file",
    "source_unlinked", "placement_unverified", "ambiguous_match",
}
_COUNTS = _CODES | {
    "staged_visible_in_library", "published_linked_to_origin",
    "separate_local_and_remote_cards", "unlinked_receipts_staged",
}
_STATES = {"active", "inactive", "unknown"}
_KINDS = {"purchase", "loan", "notice", "internal"}
_RECEIPT_STATES = {
    "discovered", "pending", "acquiring", "complete",
    "retryable_error", "attention_required", "failed", "unknown",
}
_EVENT_OPERATIONS = {
    "sync_start", "sync_result", "inventory", "admission", "delivery",
    "staging", "adept", "publication", "indexing", "origin_link",
    "maintenance", "recovery", "lock", "error",
}
_EVENT_STATES = {"success", "partial", "failed", "busy", "skipped", "cancelled", "unknown"}
_EVENT_CODES = {
    "source_disabled", "operation_busy", "source_sync_partial",
    "http_error", "preflight_failed", "publication_failed",
    "acquisition_delivery", "source_not_configured", "account_ambiguous",
    "sync_unavailable", "cancelled", "unknown",
}
_SOURCE_BOOL_FIELDS = (
    "synchronizable", "local_artifact_found", "origin_link_present",
    "origin_link_account_match", "published_final_placement", "staging_only",
    "ui_projection_linked", "staged_visible_in_library",
    "separate_local_and_remote_cards",
)


def _require_ref(value: Any) -> str:
    if type(value) is not str or not _REF.fullmatch(value):
        raise ValueError("report_opaque_reference_invalid")
    return value


def _safe_audit(value: Any) -> dict[str, Any]:
    """Reconstruct only closed Core evidence, never stringify raw payloads."""
    if type(value) is not dict or value.get("schema_version") != 1:
        raise ValueError("report_audit_invalid")
    availability = value.get("snapshot_availability", "available")
    if availability not in ("available", "unavailable"):
        raise ValueError("report_audit_invalid")
    count = value.get("source_count")
    receipts = value.get("receipt_count")
    rows = value.get("sources")
    receipt_rows = value.get("receipts")
    if (type(count) is not int or not 0 <= count <= _MAX_ROWS
            or type(receipts) is not int or not 0 <= receipts <= _MAX_ROWS
            or type(rows) is not list or type(receipt_rows) is not list
            or len(rows) != count or len(receipt_rows) != receipts):
        raise ValueError("report_audit_invalid")
    safe_sources = []
    for item in rows:
        if type(item) is not dict or item.get("code") not in _CODES:
            raise ValueError("report_audit_source_invalid")
        kind, state, basis = item.get("kind"), item.get("source_state"), item.get("match_basis")
        if kind not in _KINDS or state not in _STATES or basis not in ("origin_exact", "none"):
            raise ValueError("report_audit_source_invalid")
        if any(type(item.get(key)) is not bool for key in _SOURCE_BOOL_FIELDS):
            raise ValueError("report_audit_source_invalid")
        safe_sources.append({
            "source_ref": _require_ref(item.get("source_ref")), "kind": kind,
            "source_state": state, "code": item["code"], "match_basis": basis,
            **{key: item[key] for key in _SOURCE_BOOL_FIELDS},
        })
    safe_receipts = []
    for item in receipt_rows:
        if type(item) is not dict or item.get("state") not in _RECEIPT_STATES:
            raise ValueError("report_audit_receipt_invalid")
        attempts = item.get("attempt_count")
        if (type(item.get("artifact_recorded")) is not bool
                or attempts is not None and (type(attempts) is not int
                                             or not 0 <= attempts <= 10_000)):
            raise ValueError("report_audit_receipt_invalid")
        flags = ("physical_artifact_found", "staging_only",
                 "published_final_placement", "final_file_sha_verified",
                 "staged_visible_in_library")
        if (any(type(item.get(flag)) is not bool for flag in flags)
                or item.get("origin_link_present") not in (None, False)):
            # A receipt must never claim an exact provider link by SHA alone.
            raise ValueError("report_audit_receipt_invalid")
        code = item.get("error_code")
        safe_receipts.append({
            "receipt_ref": _require_ref(item.get("receipt_ref")),
            "state": item["state"], "attempt_count": attempts,
            "artifact_recorded": item["artifact_recorded"],
            "error_code": code if type(code) is str and code in _EVENT_CODES else "unknown",
            "origin_link_present": item.get("origin_link_present"),
            **{flag: item[flag] for flag in flags},
        })
    counts = value.get("counts")
    if type(counts) is not dict or any(
        key not in _COUNTS or type(n) is not int or n < 0 or n > _MAX_ROWS
        for key, n in counts.items()
    ):
        raise ValueError("report_counts_invalid")
    coverage = value.get("coverage")
    if type(coverage) is not dict:
        raise ValueError("report_coverage_invalid")
    return {
        "schema_version": 1, "scope": "exact_account",
        "snapshot_availability": availability,
        "scope_ref": _require_ref(value.get("scope_ref")), "read_only": True,
        "network": False, "source_count": count, "receipt_count": receipts,
        "counts": dict(sorted(counts.items())), "sources": safe_sources,
        "receipts": safe_receipts,
        "coverage": {
            "source_references": "persisted_only" if availability == "available" else "unavailable",
            "staging": ("configured_roots_only" if coverage.get("staging") == "configured_roots_only"
                        else "unavailable"),
            "filesystem": ("sha_checked" if coverage.get("filesystem") == "sha_checked"
                           else "presence_only"),
            "provider_inventory": "not_refreshed",
            "catalogue_matching": "exact_origin_only",
            "legacy_receipts": "exact_account_artifacts_no_source_inference",
        },
    }


def _closed_events(
    events: Any, *, scope_ref: str, run_ref: str,
) -> list[dict[str, str]]:
    """Only events explicitly tagged with this exact account's opaque scope."""
    if type(events) not in (tuple, list) or len(events) > _MAX_ROWS:
        raise ValueError("report_events_invalid")
    output = []
    salt = os.urandom(16)
    for event in events:
        if type(event) is not dict:
            raise ValueError("report_events_invalid")
        op, state, code = (event.get("operation"), event.get("status"),
                           event.get("code"))
        if (type(op) is not str or op not in _EVENT_OPERATIONS
                or type(state) is not str or state not in _EVENT_STATES
                or event.get("scope_ref") != scope_ref
                or event.get("run_ref") != run_ref):
            # Never include events from another account or unrelated run.
            raise ValueError("report_events_scope_invalid")
        row = {
            "run_ref": run_ref, "operation": op, "status": state,
            "code": code if type(code) is str and code in _EVENT_CODES else "unknown",
        }
        task = event.get("task_ref")
        if task is not None:
            if type(task) is not str or not 0 < len(task) <= 512:
                raise ValueError("report_events_invalid")
            row["task_ref"] = hashlib.sha256(
                salt + task.encode("utf-8", "surrogatepass")
            ).hexdigest()[:24]
        output.append(row)
    return output


def _utc(value: datetime) -> str:
    if (type(value) is not datetime or value.tzinfo is None
            or value.utcoffset() is None):
        raise ValueError("report_clock_invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _json_bytes(obj: Any) -> bytes:
    data = (json.dumps(obj, sort_keys=True, ensure_ascii=False,
                       allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > _MAX_BYTES:
        raise ValueError("report_size_limit")
    return data


def write_sync_report(
    destination: Path, *, provider_id: str, run_ref: str,
    started_at: datetime, finished_at: datetime, exit_code: int,
    sync_result: dict[str, Any], before: dict[str, Any],
    after: dict[str, Any], events: tuple[dict[str, str], ...] = (),
) -> Path:
    """Write a five-entry ZIP with 0600 permissions; never overwrite a path.

    Call in the sync CLI's finally block, including failed/partial runs.
    Preflight refusal before any admitted run must not call this function.
    """
    if (not isinstance(destination, Path) or destination.suffix.lower() != ".zip"
            or type(provider_id) is not str or not _PROVIDER.fullmatch(provider_id)
            or type(run_ref) is not str or not _REF.fullmatch(run_ref)
            or type(exit_code) is not int or not 0 <= exit_code <= 255):
        raise ValueError("report_request_invalid")
    start = _utc(started_at)
    end = _utc(finished_at)
    if finished_at < started_at:
        raise ValueError("report_clock_invalid")
    selected_before = _safe_audit(before)
    selected_after = _safe_audit(after)
    if selected_before["scope_ref"] != selected_after["scope_ref"]:
        raise ValueError("report_scope_changed")
    safe_events = _closed_events(events, scope_ref=selected_before["scope_ref"], run_ref=run_ref)
    summary = safe_sync_result(sync_result)
    if type(summary) is not dict:
        raise ValueError("report_result_invalid")
    identity = build_identity()  # same canonical receipt as startup and doctor
    manifest = {
        "schema_version": 1, "format": "cifereca-safe-sync-report",
        "build_identity": identity, "provider_id": provider_id,
        "run_ref": run_ref, "source_scope_ref": selected_before["scope_ref"],
        "started_at": start, "finished_at": end,
        "exit_code": exit_code, "status": summary["status"],
        "events_coverage": "cli_phases_only" if safe_events else "unavailable",
        "http_trace_included": False, "file_paths_included": False,
        "provenance": "local_snapshots_not_provider_inventory",
    }
    lines = [
        "Cifereca — rapport de synchronisation ciblée",
        f"Provider: {provider_id}", f"Statut: {summary['status']}",
        f"Exit: {exit_code}", f"Build: {identity['commit_sha'] or 'unavailable'}",
        f"Sources avant: {selected_before['source_count'] if selected_before['snapshot_availability'] == 'available' else 'unavailable'}",
        f"Sources après: {selected_after['source_count'] if selected_after['snapshot_availability'] == 'available' else 'unavailable'}",
        "État des placements (après) :",
    ]
    for key, number in selected_after["counts"].items():
        lines.append(f"  {key}: {number}")
    lines += [
        "Les notices locales ne prouvent pas une publication ou un OriginLink.",
        "La trace HTTP brute n'est pas incluse. Événements: "
        + ("corrélés et fermés" if safe_events else "non capturés"),
    ]
    files = {
        "manifest.json": _json_bytes(manifest),
        "sync-result.json": _json_bytes(summary),
        "acquisition-audit-before.json": _json_bytes(selected_before),
        "acquisition-audit-after.json": _json_bytes(selected_after),
        "events.jsonl": b"".join(_json_bytes(event) for event in safe_events),
        "summary.txt": ("\n".join(lines) + "\n").encode("utf-8"),
    }
    if sum(len(data) for data in files.values()) > _MAX_BYTES:
        raise ValueError("report_size_limit")
    if os.path.lexists(destination):
        raise FileExistsError("report_destination_exists")
    if not destination.parent.is_dir():
        raise ValueError("report_parent_missing")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(
            prefix=".cifereca-sync-", suffix=".part", dir=destination.parent,
        )
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            with ZipFile(stream, "w", compression=ZIP_DEFLATED,
                         compresslevel=6, allowZip64=False) as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic, exclusive publication even if another process races us.
        os.link(temporary, destination, follow_symlinks=False)
        return destination
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
