"""#3882: bounded, source-scoped, non-sensitive sync report ZIP.

This first slice reports canonical SQLite *counts* and CLI milestones only.
It does NOT claim a filesystem SHA audit, HTTP receipt verification, or a
complete correlated Core event stream. Those are separate gates of #3882.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import logging
import threading
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
import tempfile
import zipfile

_REPORT_SCHEMA = "cifereca.sync-report/1"
_STATUSES = frozenset({"success", "complete", "partial", "failed", "busy", "skipped", "cancelled"})
_REASON_CODES = frozenset({
    "source_disabled", "operation_busy", "source_not_found", "source_not_configured",
    "source_sync_partial", "account_ambiguous", "cancelled", "sync_unavailable",
    "invalid_sync_result", "source_sync_failed", "source_sync_cancelled",
})
_KINDS = frozenset({"purchase", "loan", "notice", "internal"})
_STATES = frozenset({"active", "inactive", "unknown"})
_LEDGER = frozenset({
    "complete", "partial", "pending", "failed", "staged", "downloaded",
    "canonical_receipt_staged", "aborted", "cancelled", "skipped",
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _count(rows, allowed: frozenset[str]) -> dict[str, int]:
    data = {}
    for key, amount in rows:
        label = key if type(key) is str and key in allowed else "other"
        data[label] = data.get(label, 0) + max(0, min(int(amount), 1_000_000))
    return data


def audit_source_counts(storage, account, *, pseudonym_key: bytes | None = None,
                        library_root: Path | None = None,
                        verify_files: bool = False) -> dict[str, object]:
    """One exact persisted source scope; no network and no private rows."""
    coords = (
        account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    )
    # The single SQLite snapshot must not depend on mutable provider sessions.
    with storage._lock, storage._connect() as db:
        rights = db.execute(
            "SELECT reference_kind,COUNT(*) FROM source_references "
            "WHERE identity_id=? AND provider_id=? AND credential_id=? "
            "AND account_id=? GROUP BY reference_kind", coords,
        ).fetchall()
        states = db.execute(
            "SELECT state,COUNT(*) FROM source_references "
            "WHERE identity_id=? AND provider_id=? AND credential_id=? "
            "AND account_id=? GROUP BY state", coords,
        ).fetchall()
        origins = db.execute(
            "SELECT COUNT(*) FROM origin_links AS o "
            "JOIN source_references AS s "
            "ON s.source_reference_id=o.source_reference_id "
            "WHERE s.identity_id=? AND s.provider_id=? AND s.credential_id=? "
            "AND s.account_id=? AND o.identity_id=s.identity_id", coords,
        ).fetchone()[0]
        placements = db.execute(
            "SELECT COUNT(DISTINCT p.placement_id) FROM placements AS p "
            "JOIN origin_links AS o ON o.artifact_id=p.artifact_id "
            "JOIN source_references AS s "
            "ON s.source_reference_id=o.source_reference_id "
            "WHERE s.identity_id=? AND s.provider_id=? AND s.credential_id=? "
            "AND s.account_id=? AND p.identity_id=s.identity_id "
            "AND o.identity_id=s.identity_id", coords,
        ).fetchone()[0]
        receipts = db.execute(
            "SELECT status,COUNT(*) FROM acquisition_ledger "
            "WHERE identity_id=? AND provider_id=? AND credential_id=? "
            "AND account_id=? GROUP BY status", coords,
        ).fetchall()
        from .acquisition_staging_evidence import receipt_staging_evidence
        # Exact account only; no title/SHA-only provenance attribution.
        staging = receipt_staging_evidence(
            db, coords=coords, staging_root=(storage.path.parent / "acquisition-work")
            if isinstance(getattr(storage, "path", None), Path) else None,
            # Hashing staging bytes must be explicitly requested: routine
            # Sync summaries use verify_files=False and must not read every
            # potentially multi-GB EPUB/PDF only to count receipts.
            verify_files=verify_files, reference=lambda _ref: "private",
            canonical_root=library_root,
        )
    from .acquisition_audit import inspect_exact_source
    per_resource = inspect_exact_source(
        storage, account, library_root=library_root,
        verify_files=verify_files, pseudonym_key=pseudonym_key,
    )
    return {
        "schema_version": 1, "audit_scope": "exact_source",
        "resource_evidence": per_resource,
        "physical_verification": "bounded_sha256_checked" if verify_files else "not_performed",
        "catalogue_projection_verification": "not_performed",
        "rights_by_kind": _count(rights, _KINDS),
        "rights_by_state": _count(states, _STATES),
        "origin_link_count": min(max(int(origins), 0), 1_000_000),
        "placement_reference_count": min(max(int(placements), 0), 1_000_000),
        "ledger_by_status": _count(receipts, _LEDGER),
        "staging_evidence_coverage": staging["coverage"],
        "staged_sha_verified_count": staging["staged_verified"],
        "staged_visible_in_library_count": staging["staged_visible_in_library"],
        # None of these counters assert exact OriginLink or publication.
    }


def _safe_result(result: dict[str, object]) -> dict[str, object]:
    # The CLI and ZIP must use exactly the same closed Core projection.
    from .sync_cli_contract import safe_sync_result
    return safe_sync_result(result)


# ZIP audit members are closed DTOs, not arbitrary dictionaries. Even a
# provider error accidentally placed in an audit snapshot must not become an
# unconfirmed raw credential export (#3882).
_AUDIT_RIGHT_CLASSES = frozenset({
    "source_unlinked", "source_linked_unplaced", "ambiguous_match",
    "local_unverified", "complete_local", "missing_file",
})
_AUDIT_RECEIPT_CLASSES = frozenset({"staged_unpublished", "receipt_recorded"})
_AUDIT_FILE_STATES = frozenset({
    "not_requested", "scope_unavailable", "missing_file",
    "invalid_placement", "file_changed", "sha_mismatch",
    "file_unavailable", "verified_sha256",
    "multiple_placements_unverified", "multiple_placements_all_verified",
    "multiple_placements_mixed", "multiple_placements_none_verified",
})
_AUDIT_STAGING_STATES = frozenset({
    "not_attested", "indexed_staging_unverified", "verified_staging_file",
    "staging_file_missing", "staging_unavailable",
    "core_staged_unverified", "core_staged_untrusted",
    "core_staged_missing", "core_staged_present_unpublished",
    "core_staged_origin_recorded",
})
_AUDIT_STAGING_COVERAGE = frozenset({
    "indexed_account_receipts_only", "indexed_and_core_receipts",
    "unavailable", "truncated",
})


def _safe_count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 1_000_000 else None


def _safe_enum(value: object, allowed: frozenset[str], fallback: str) -> str:
    return value if type(value) is str and value in allowed else fallback


def _safe_reference(value: object) -> str | None:
    if (type(value) is str and len(value) == 64
            and all(char in "0123456789abcdef" for char in value)):
        return value
    return None


def _safe_counts(value: object, allowed: frozenset[str]) -> dict[str, int]:
    if type(value) is not dict:
        return {}
    result: dict[str, int] = {}
    for key, amount in value.items():
        if type(key) is str and key in (allowed | {"other"}):
            count = _safe_count(amount)
            if count is not None:
                result[key] = count
    return result


def _safe_staging(value: object) -> dict[str, object]:
    """Export only attributable staging observations, never inferred origin.

    Invalid/missing receipt refs or rows make coverage incomplete. A staging
    receipt is not proof of an OriginLink even when its file SHA is verified.
    """
    if type(value) is not dict:
        return {}
    result: dict[str, object] = {}
    if "coverage" in value:
        result["coverage"] = _safe_enum(
            value["coverage"], _AUDIT_STAGING_COVERAGE, "unavailable",
        )
    rows = value.get("observations")
    if type(rows) is not list:
        # An indexed coverage claim without its rows cannot be verified.
        if result.get("coverage") == "indexed_account_receipts_only":
            result["coverage"] = "unavailable"
        return result
    observations: list[dict[str, object]] = []
    truncated = len(rows) > 512
    for row in rows[:512]:
        if type(row) is not dict:
            truncated = True
            continue
        ref = _safe_reference(row.get("receipt_ref"))
        if ref is None:
            # A receipt without a pseudonymized exact binding is not
            # attributable to this run; do not export a phantom row.
            truncated = True
            continue
        raw_state = row.get("staging_state")
        state = _safe_enum(raw_state, _AUDIT_STAGING_STATES, "not_attested")
        if state != raw_state:
            truncated = True
        verified = row.get("staging_sha_verified") is True
        visible = row.get("staged_visible_in_library") is True
        if verified and state not in (
            "verified_staging_file", "core_staged_present_unpublished",
            "core_staged_origin_recorded",
        ):
            verified = False
            truncated = True
        if visible and not verified:
            visible = False
            truncated = True
        if row.get("origin_proven_by_receipt") is True:
            # This read-only receipt inspection has no OriginLink proof.
            truncated = True
        for flag in ("staging_sha_verified", "staged_visible_in_library",
                     "origin_proven_by_receipt"):
            if flag in row and type(row[flag]) is not bool:
                truncated = True
        observations.append({
            "receipt_ref": ref, "staging_state": state,
            "staging_sha_verified": verified,
            "staged_visible_in_library": visible,
            "origin_proven_by_receipt": False,
        })
    result["observations"] = observations
    # Counts are derived from the rows actually exported, not a possibly
    # inconsistent aggregate on an unexpected input object.
    for key, field in (
        ("staged_verified", "staging_sha_verified"),
        ("staged_visible_in_library", "staged_visible_in_library"),
    ):
        positive = sum(row[field] is True for row in observations)
        raw_count = _safe_count(value.get(key))
        if raw_count is None or raw_count != positive:
            truncated = True
        result[key] = positive
    if truncated:
        result["coverage"] = "truncated"
    return result


def _safe_resource_evidence(value: object) -> dict[str, object]:
    if type(value) is not dict:
        return {}
    result: dict[str, object] = {}
    if value.get("schema_version") == 1 and type(value.get("schema_version")) is int:
        result["schema_version"] = 1
    if value.get("scope") == "exact_source":
        result["scope"] = "exact_source"
    if value.get("network") == "not_used":
        result["network"] = "not_used"
    if value.get("mutations") == "none":
        result["mutations"] = "none"
    if type(value.get("verify_files")) is bool:
        result["verify_files"] = value["verify_files"]
    if type(value.get("truncated")) is bool:
        result["truncated"] = value["truncated"]
    for key, classification_set in (
        ("rights", _AUDIT_RIGHT_CLASSES),
        ("receipts", _AUDIT_RECEIPT_CLASSES),
    ):
        rows = value.get(key)
        if type(rows) is not list:
            if key in value:
                result["truncated"] = True
            continue
        records: list[dict[str, object]] = []
        for row in rows[:512]:
            if type(row) is not dict:
                result["truncated"] = True
                continue
            item: dict[str, object] = {
                "classification": _safe_enum(
                    row.get("classification"), classification_set, "unclassified",
                ),
            }
            ref_key = "resource_ref" if key == "rights" else "receipt_ref"
            ref = _safe_reference(row.get(ref_key))
            if ref is not None:
                item[ref_key] = ref
            if key == "rights":
                item["kind"] = _safe_enum(row.get("kind"), _KINDS | {"other"}, "other")
                item["right_state"] = _safe_enum(row.get("right_state"), _STATES, "unknown")
                item["file_verification"] = _safe_enum(
                    row.get("file_verification"), _AUDIT_FILE_STATES, "not_requested",
                )
                for count_key in ("origin_link_count", "placement_count"):
                    count = _safe_count(row.get(count_key))
                    if count is not None:
                        item[count_key] = count
                if item["file_verification"].startswith("multiple_placements_"):
                    # The three bounded aggregate counters are a unit; do
                    # not export contradictory or incomplete physical proof.
                    fields = (
                        "verified_placement_count", "missing_placement_count",
                        "unverified_placement_count",
                    )
                    amounts = [_safe_count(row.get(field)) for field in fields]
                    total = item.get("placement_count")
                    valid = (
                        value.get("verify_files") is True
                        and type(total) is int and total > 1
                        and all(amount is not None for amount in amounts)
                        and sum(amounts) == total
                        and (
                            (item["file_verification"] == "multiple_placements_unverified"
                             and amounts == [0, 0, total])
                            or (item["file_verification"] == "multiple_placements_all_verified"
                                and amounts == [total, 0, 0])
                            or (item["file_verification"] == "multiple_placements_mixed"
                                and 0 < amounts[0] < total)
                            or (item["file_verification"] == "multiple_placements_none_verified"
                                and amounts[0] == 0)
                        )
                    )
                    if valid:
                        item.update(zip(fields, amounts))
                    else:
                        item["file_verification"] = "multiple_placements_unverified"
                        result["truncated"] = True
            else:
                item["status"] = _safe_enum(row.get("status"), _LEDGER | {"other"}, "other")
            records.append(item)
        result[key] = records
        if len(rows) > 512:
            result["truncated"] = True
    if "staging_evidence" in value:
        staging = _safe_staging(value["staging_evidence"])
        result["staging_evidence"] = staging
        if staging.get("coverage") == "truncated":
            result["truncated"] = True
    return result


def _safe_audit_snapshot(value: object) -> dict[str, object]:
    """Only canonical evidence types can enter the exported before/after ZIP."""
    if type(value) is not dict:
        return {"status": "unavailable", "reason": "audit_unavailable"}
    if value.get("status") == "unavailable":
        return {"status": "unavailable", "reason": "audit_unavailable"}
    result: dict[str, object] = {}
    if value.get("schema_version") == 1 and type(value.get("schema_version")) is int:
        result["schema_version"] = 1
    if value.get("audit_scope") == "exact_source":
        result["audit_scope"] = "exact_source"
    if "resource_evidence" in value:
        result["resource_evidence"] = _safe_resource_evidence(value["resource_evidence"])
    if "physical_verification" in value:
        result["physical_verification"] = _safe_enum(
            value["physical_verification"],
            frozenset({"not_performed", "bounded_sha256_checked"}), "not_performed",
        )
    if "catalogue_projection_verification" in value:
        result["catalogue_projection_verification"] = _safe_enum(
            value["catalogue_projection_verification"], frozenset({"not_performed"}),
            "not_performed",
        )
    for key, allowed in (
        ("rights_by_kind", _KINDS),
        ("rights_by_state", _STATES),
        ("ledger_by_status", _LEDGER),
    ):
        if key in value:
            result[key] = _safe_counts(value[key], allowed)
    for key in (
        "origin_link_count", "placement_reference_count",
        "staged_sha_verified_count", "staged_visible_in_library_count",
    ):
        count = _safe_count(value.get(key))
        if count is not None:
            result[key] = count
    if "staging_evidence_coverage" in value:
        result["staging_evidence_coverage"] = _safe_enum(
            value["staging_evidence_coverage"], _AUDIT_STAGING_COVERAGE,
            "unavailable",
        )
    return result


@contextmanager
def collect_source_sync_core_events(provider_id: str):
    """Observe only attested Core events inside this exact synchronous CLI call.

    No journal replay, timestamp pairing or global log-level changes. Events
    from other threads/sources and arbitrary plugin log messages are rejected.
    """
    from .diagnostic_source_sync import public_fields

    records: list[dict[str, object]] = []
    worker = threading.get_ident()

    class _CoreObserver(logging.Handler):
        def emit(self, record):
            if threading.get_ident() != worker or len(records) >= 33:
                return
            payload = getattr(record, "_cifereca_journal_payload", None)
            if (type(payload) is not dict or payload.get("category") != "SYNC"
                    or payload.get("operation") != "source sync"):
                return
            fields = public_fields(payload.get("detail"))
            if fields and fields["provider_id"] == provider_id:
                records.append(fields)

    logger = logging.getLogger("cifereca")
    handler = _CoreObserver()
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def _closed_core_events(
    events: object, *, run_ref: str, provider_id: str,
) -> tuple[list[dict[str, object]], bool]:
    """Allow only existing Core source-sync facts from the selected invocation."""
    from .diagnostic_source_sync import (
        CANONICAL_EXECUTION_STATUSES, public_fields,
    )

    accepted: list[dict[str, object]] = []
    truncated = False
    if type(events) is not list:
        return accepted, False
    for item in events:
        if len(accepted) >= 32:
            truncated = True
            break
        fields = public_fields(item)
        if not fields or fields["provider_id"] != provider_id:
            truncated = True
            continue
        row: dict[str, object] = {
            "run_ref": run_ref, "origin": "core_source_sync",
            "provider_id": fields["provider_id"],
            "phase": fields["phase"], "status": fields["status"],
            "code": fields["code"],
        }
        for key in ("task_ref", "duration_ms", "loans", "library", *(
            "canonical_" + state for state in CANONICAL_EXECUTION_STATUSES
        )):
            if key in fields:
                row[key] = fields[key]
        accepted.append(row)
    return accepted, truncated


# CLI phases are observations made by this command, never Core/HTTP proof.
# A provider-controlled exception, URL or task payload is not a report event.
_CLI_EVENT_PHASES = frozenset({
    "selected", "audit_before", "connect", "sync", "trace_export",
    "audit_after",
})
_CLI_EVENT_STATES = frozenset({
    "started", "success", "complete", "partial", "failed", "busy",
    "skipped", "cancelled", "unavailable",
})
_MAX_CLI_EVENTS = 32


def _closed_cli_events(
    events: object, *, run_ref: str, outcome: dict[str, object],
) -> tuple[list[dict[str, object]], bool]:
    """Only bounded closed CLI observations for this run enter events.jsonl."""
    accepted: list[dict[str, object]] = []
    truncated = False
    if type(events) is list:
        truncated = len(events) > _MAX_CLI_EVENTS
        for item in events[:_MAX_CLI_EVENTS]:
            if type(item) is not dict:
                truncated = True
                continue
            phase, status = item.get("phase"), item.get("status")
            if (type(phase) is not str or phase not in _CLI_EVENT_PHASES
                    or type(status) is not str or status not in _CLI_EVENT_STATES):
                truncated = True
                continue
            row: dict[str, object] = {"run_ref": run_ref, "phase": phase, "status": status}
            code = item.get("code")
            if type(code) is str and code in _REASON_CODES:
                row["code"] = code
            accepted.append(row)
    else:
        accepted.append({"run_ref": run_ref, "phase": "admitted", "status": "started"})
    finished: dict[str, object] = {
        "run_ref": run_ref, "phase": "run_finished", "status": outcome["status"],
    }
    if "code" in outcome:
        finished["code"] = outcome["code"]
    accepted.append(finished)
    return accepted, truncated


def _json_bytes(value: object) -> bytes:
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True) + "\n").encode("ascii")
    if len(payload) > 512 * 1024:
        raise ValueError("sync_report_size_limit")
    return payload



# Closed summary vocabulary: never interpolate provider data, filesystem paths,
# HTTP diagnostics or arbitrary exception messages into the shareable report.
_SUMMARY_COUNTS = ("scanned", "downloaded", "completed", "unchanged", "pending", "errors")
_SUMMARY_SNAPSHOT_COUNTS = (
    "origin_link_count", "placement_reference_count",
    "staged_sha_verified_count", "staged_visible_in_library_count",
)
_SUMMARY_RIGHT_CLASSES = (
    "source_unlinked", "source_linked_unplaced", "missing_file",
    "ambiguous_match", "local_unverified", "complete_local",
)
_SUMMARY_RECEIPT_CLASSES = ("staged_unpublished", "receipt_recorded")


def _safe_summary_count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 1_000_000 else None


def _summary_audit(phase: str, audit: object) -> list[str]:
    """Describe exact-source snapshots; never attribute their deltas to a run."""
    if type(audit) is not dict or audit.get("status") == "unavailable":
        return [f"audit_{phase}: unavailable"]
    has_evidence = type(audit.get("resource_evidence")) is dict or any(
        _safe_summary_count(audit.get(field)) is not None
        for field in _SUMMARY_SNAPSHOT_COUNTS
    ) or type(audit.get("rights_by_kind")) is dict
    if not has_evidence:
        return [f"audit_{phase}: unavailable"]
    lines = [f"audit_{phase}: available"]
    for field in _SUMMARY_SNAPSHOT_COUNTS:
        value = _safe_summary_count(audit.get(field))
        if value is not None:
            lines.append(f"snapshot_{phase}_{field}: {value}")
    evidence = audit.get("resource_evidence")
    if type(evidence) is not dict:
        return lines
    if evidence.get("truncated") is True:
        lines.append(f"snapshot_{phase}_resources_truncated: true")
    if phase != "after":
        return lines
    # These are classifications, not necessarily failures. In particular a
    # catalogue-only reference may legitimately have no local OriginLink.
    for group, classes in (
        ("rights", _SUMMARY_RIGHT_CLASSES),
        ("receipts", _SUMMARY_RECEIPT_CLASSES),
    ):
        records = evidence.get(group)
        if type(records) is not list:
            continue
        for category in classes:
            count = sum(
                type(item) is dict and item.get("classification") == category
                for item in records[:512]
            )
            if count:
                lines.append(f"observed_{group}_{category}: {count}")
    return lines


def _report_summary(
    *, outcome: dict[str, object], exit_code: int, before: object,
    after: object, evidence_coverage: str, last_cli_phase: str | None = None,
    issue_cli_phase: str | None = None,
    core_event_coverage: str = "cli_milestones_only",
) -> bytes:
    """Bounded human-readable overview of evidence already in the ZIP.

    This output is intentionally NOT a synthesis of the provider's raw result:
    it only copies closed status/counters and canonical, local snapshots.
    """
    lines = [
        "CIFERECA SOURCE SYNC REPORT",
        "status: " + (
            outcome["status"] if type(outcome.get("status")) is str
            and outcome["status"] in _STATUSES else "failed"
        ),
        f"exit_code: {exit_code}",
        f"coverage: {evidence_coverage}; " + (
            "same-call Core source-sync evidence; no per-item/HTTP proof"
            if core_event_coverage == "cli_and_core_source_sync"
            else "no HTTP/correlated journal proof"
        ),
    ]
    if type(last_cli_phase) is str and last_cli_phase in _CLI_EVENT_PHASES:
        lines.append(f"last_cli_phase: {last_cli_phase}")
    if type(issue_cli_phase) is str and issue_cli_phase in _CLI_EVENT_PHASES:
        lines.append(f"issue_cli_phase: {issue_cli_phase}")
    reason = outcome.get("code")
    if type(reason) is str and reason in _REASON_CODES:
        lines.append(f"reason_code: {reason}")
    for field in _SUMMARY_COUNTS:
        count = _safe_summary_count(outcome.get(field))
        if count is not None:
            lines.append(f"reported_{field}: {count}")
    lines.extend(_summary_audit("before", before))
    lines.extend(_summary_audit("after", after))
    lines.append(
        "note: snapshots describe one source at two instants, not per-run "
        "causality or proof of final Placement/OriginLink."
    )
    lines.append(
        "note: omitted classes are unobserved in the bounded sample; "
        "absence is not an exhaustive negative proof."
    )
    return ("\n".join(lines) + "\n").encode("ascii")


def write_sync_report(
    destination: Path, *, account, run_ref: str,
    before: dict[str, object], after: dict[str, object],
    result: dict[str, object], started_at: str, exit_code: int,
    cli_events: list[dict[str, object]] | None = None,
    core_events: list[dict[str, object]] | None = None,
) -> None:
    """Atomic non-overwriting ZIP; no raw HTTP captures or personal identifiers."""
    if not isinstance(destination, Path) or destination.name in {"", ".", ".."}:
        raise ValueError("sync_report_invalid_destination")
    if type(run_ref) is not str or len(run_ref) != 32 or any(c not in "0123456789abcdef" for c in run_ref):
        raise ValueError("sync_report_invalid_reference")
    # An admitted CLI run interrupted by SIGINT reports shell exit 130.
    # It is a cancelled run, not a failed report write or success.
    if type(exit_code) is not int or exit_code not in (0, 2, 130):
        raise ValueError("sync_report_invalid_exit")
    target = Path(os.path.abspath(destination.expanduser()))
    parent = target.parent
    if (os.path.lexists(target) or not parent.is_dir()
            or any(part.is_symlink() for part in (target, *target.parents))):
        raise FileExistsError("sync_report_destination_unavailable")
    from ..startup_revision import build_identity
    identity = build_identity()
    # A per-run salted fingerprint prevents account/credential disclosure and
    # cross-export stable matching of pseudonyms.
    coords = (account.identity_id, account.provider_id,
              account.credential_id, account.unique_id)
    # The HMAC key is ephemeral and never exported: even guessable local IDs
    # cannot be recovered by brute-forcing the public report run_ref.
    selection_ref = hmac.new(
        secrets.token_bytes(32),
        json.dumps(coords, separators=(",", ":"), ensure_ascii=True).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    safe_result = _safe_result(result)
    events, events_truncated = _closed_cli_events(
        cli_events, run_ref=run_ref, outcome=safe_result,
    )
    core_observations, core_truncated = _closed_core_events(
        core_events, run_ref=run_ref, provider_id=account.provider_id,
    )
    # A Core terminal code may enrich the projected business result only if
    # its exact source and terminal status agree with the returned Core value.
    matching_terminals = [
        event for event in core_observations if event["phase"] == "completed"
        and event["status"] == safe_result["status"]
    ]
    if len(matching_terminals) == 1 and "code" not in safe_result:
        safe_result["code"] = matching_terminals[0]["code"]
        events[-1]["code"] = safe_result["code"]
    # Keep CLI milestones before/after in their original order; insert Core
    # observations at the synchronous source-sync boundary, never at an
    # inferred timestamp from an unrelated task.
    sync_end = next((
        i for i, event in enumerate(events)
        if event["phase"] == "sync" and event["status"] != "started"
    ), len(events) - 1)
    events[sync_end:sync_end] = core_observations
    cli_phases = [
        event["phase"] for event in events if event["phase"] in _CLI_EVENT_PHASES
    ]
    issue_phases = [
        event["phase"] for event in events
        if event["phase"] in _CLI_EVENT_PHASES
        and event["status"] in {"failed", "partial", "busy", "cancelled"}
    ]
    before = _safe_audit_snapshot(before)
    after = _safe_audit_snapshot(after)
    ended = _utc_now()
    manifest = {
        "schema": _REPORT_SCHEMA,
        "run_ref": run_ref,
        "source_ref": selection_ref,
        "started_at": started_at,
        "completed_at": ended,
        "status": safe_result["status"],
        "exit_code": exit_code,
        "evidence_coverage": (
            "local_audit_unavailable"
            if before.get("status") == "unavailable"
            or after.get("status") == "unavailable"
            else "canonical_sqlite_graph_with_bounded_sha256"
            if before.get("physical_verification") == "bounded_sha256_checked"
            and after.get("physical_verification") == "bounded_sha256_checked"
            else "canonical_sqlite_graph_without_filesystem"
        ),
        "event_coverage": (
            "cli_and_core_source_sync"
            if matching_terminals else "cli_milestones_only"
        ),
        "cli_events_truncated": events_truncated,
        "core_events_truncated": core_truncated,
        "core_source_sync_events": len(core_observations),
        "cli_last_phase": cli_phases[-1] if cli_phases else "not_observed",
        "cli_issue_phase": issue_phases[-1] if issue_phases else "none",
        "build_identity": identity,
    }
    summary = _report_summary(
        outcome=safe_result, exit_code=exit_code, before=before, after=after,
        evidence_coverage=manifest["evidence_coverage"],
        last_cli_phase=cli_phases[-1] if cli_phases else None,
        issue_cli_phase=issue_phases[-1] if issue_phases else None,
        core_event_coverage=manifest["event_coverage"],
    )
    parts = {
        "manifest.json": _json_bytes(manifest),
        "sync-result.json": _json_bytes(safe_result),
        "acquisition-audit-before.json": _json_bytes(before),
        "acquisition-audit-after.json": _json_bytes(after),
        "events.jsonl": b"".join(_json_bytes(row) for row in events),
        "summary.txt": summary,
    }
    temp: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".cifereca-report-", suffix=".tmp", dir=parent)
        temp = Path(name)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for filename, payload in parts.items():
                    z.writestr(filename, payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link publish is O_EXCL: never replace even a symlink or race.
        os.link(temp, target, follow_symlinks=False)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
        # No already-existing destination is touched if publication failed.
