"""#3882: bounded, source-scoped, non-sensitive sync report ZIP.

This first slice reports canonical SQLite *counts* and CLI milestones only.
It does NOT claim a filesystem SHA audit, HTTP receipt verification, or a
complete correlated Core event stream. Those are separate gates of #3882.
"""
from __future__ import annotations

from datetime import datetime, timezone
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
    "invalid_sync_result",
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
            verify_files=True, reference=lambda _ref: "private",
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
    status = result.get("status")
    code = result.get("code")
    outcome: dict[str, object] = {
        "status": status if type(status) is str and status in _STATUSES else "failed",
    }
    if type(code) is str and code in _REASON_CODES:
        outcome["code"] = code
    for key in ("downloaded", "completed", "scanned", "unchanged", "pending", "errors"):
        value = result.get(key)
        if type(value) is int and 0 <= value <= 1_000_000:
            outcome[key] = value
    return outcome


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
    after: object, evidence_coverage: str,
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
        f"coverage: {evidence_coverage}; no HTTP/correlated journal proof",
    ]
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
) -> None:
    """Atomic non-overwriting ZIP; no raw HTTP captures or personal identifiers."""
    if not isinstance(destination, Path) or destination.name in {"", ".", ".."}:
        raise ValueError("sync_report_invalid_destination")
    if type(run_ref) is not str or len(run_ref) != 32 or any(c not in "0123456789abcdef" for c in run_ref):
        raise ValueError("sync_report_invalid_reference")
    if type(exit_code) is not int or exit_code not in (0, 2):
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
            "canonical_sqlite_graph_with_bounded_sha256"
            if before.get("physical_verification") == "bounded_sha256_checked"
            and after.get("physical_verification") == "bounded_sha256_checked"
            else "canonical_sqlite_graph_without_filesystem"
        ),
        "event_coverage": "cli_milestones_only",
        "build_identity": identity,
    }
    events = [
        {"run_ref": run_ref, "phase": "admitted", "status": "started"},
        {"run_ref": run_ref, "phase": "sync_finished", "status": safe_result["status"],
         **({"code": safe_result["code"]} if "code" in safe_result else {})},
    ]
    summary = _report_summary(
        outcome=safe_result, exit_code=exit_code, before=before, after=after,
        evidence_coverage=manifest["evidence_coverage"],
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
