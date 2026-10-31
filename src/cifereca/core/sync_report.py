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


def audit_source_counts(storage, account) -> dict[str, object]:
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
    return {
        "schema_version": 1, "audit_scope": "exact_source",
        "physical_verification": "not_performed",
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
    outcome: dict[str, object] = {
        "status": result.get("status") if result.get("status") in _STATUSES else "failed",
    }
    if result.get("code") in _REASON_CODES:
        outcome["code"] = result["code"]
    for key in ("downloaded", "completed", "scanned", "unchanged", "pending", "errors"):
        value = result.get(key)
        if type(value) is int and 0 <= value <= 1_000_000:
            outcome[key] = value
    return outcome


def _json_bytes(value: object) -> bytes:
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True) + "\n").encode("ascii")
    if len(payload) > 65536:
        raise ValueError("sync_report_size_limit")
    return payload


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
        "evidence_coverage": "canonical_sqlite_counts_only",
        "event_coverage": "cli_milestones_only",
        "build_identity": identity,
    }
    events = [
        {"run_ref": run_ref, "phase": "admitted", "status": "started"},
        {"run_ref": run_ref, "phase": "sync_finished", "status": safe_result["status"],
         **({"code": safe_result["code"]} if "code" in safe_result else {})},
    ]
    summary = (
        "CIFERECA SOURCE SYNC REPORT\n"
        f"status: {safe_result['status']}\n"
        f"exit_code: {exit_code}\n"
        "coverage: canonical SQLite counts; no filesystem/HTTP/correlated journal proof\n"
    ).encode("ascii")
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
