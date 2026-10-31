"""P0 #3882: exact-scope, read-only acquisition/placement audit.

No provider calls, SQLite writes, catalogue-title matching or filesystem repair.
A library card cannot attest a delivered provider file without OriginLink and
an actual final placement in the same identity. Missing/unknown != absent right.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any

from ..catalog import LocalCatalog

_MAX_ROWS = 20_000
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _scope_value(value: Any, *, allow_empty: bool = False) -> bool:
    return (type(value) is str and len(value) <= 256
            and (bool(value) or allow_empty)
            and not any(char in value for char in "\x00\r\n"))


def _opaque(salt: bytes, value: Any) -> str:
    return hashlib.sha256(salt + str(value).encode("utf-8", "surrogatepass")).hexdigest()[:24]


def _inside(path: Path, base: Path) -> bool:
    try:
        return path.is_relative_to(base)
    except ValueError:
        return False


def _path_scope(
    raw: Any, *, library_root: Path, owner_directory: str,
    staging_roots: tuple[Path, ...],
) -> tuple[str, Path | None, Path | None]:
    """Classify a *stored* absolute path lexically; never infer provider from it."""
    if type(raw) is not str or not raw or "\x00" in raw:
        return "unknown", None, None
    supplied = Path(raw)
    if not supplied.is_absolute() or ".." in supplied.parts:
        return "unknown", None, None
    path = Path(os.path.abspath(supplied))
    final_root = Path(os.path.abspath(library_root.expanduser())) / owner_directory
    if (owner_directory and owner_directory not in (".", "..")
            and "/" not in owner_directory and "\\" not in owner_directory
            and _inside(path, final_root) and path != final_root):
        return "final", path, final_root
    for root in staging_roots:
        anchor = Path(os.path.abspath(root.expanduser()))
        if _inside(path, anchor) and path != anchor:
            return "staging", path, anchor
    return "outside_known_roots", None, None


def _physical(
    path: Path | None, anchor: Path | None, *, sha: Any,
    size: Any, verify_files: bool,
) -> str:
    """Fail closed on symlink, inaccessible mounts and changed files."""
    if path is None or anchor is None:
        return "not_checked"
    try:
        # Reject every symlink up to and including the root anchor.
        current = path
        while True:
            if current.is_symlink():
                return "unsafe_symlink"
            if current == anchor:
                break
            if current == current.parent:
                return "unavailable"
            current = current.parent
        before = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            return "unavailable"
        if type(size) is int and before.st_size != size:
            return "size_mismatch"
        if not verify_files:
            return "present_unverified"
        if type(sha) is not str or _SHA256.fullmatch(sha) is None:
            return "sha_unavailable"
        digest = LocalCatalog.sha256(path)
        after = os.stat(path, follow_symlinks=False)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, f) != getattr(after, f) for f in fields):
            return "changed_during_verification"
        return "verified" if digest == sha else "sha_mismatch"
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError, RuntimeError):
        return "unavailable"


def audit_account(
    storage: Any, *, identity_id: str, provider_id: str, credential_id: str,
    account_id: str, library_root: Path,
    staging_roots: tuple[Path, ...] = (),
    verify_files: bool = False, opaque_salt: bytes | None = None,
) -> dict[str, Any]:
    """Audit an exact persisted account without side effects or negative inference.

    Staging roots are supplied by the caller's Core configuration; they are
    never a provider-specific whitelist. Without these roots the audit cannot
    claim that an unindexed local file is in staging.
    """
    if not (_scope_value(identity_id) and _scope_value(provider_id)
            and _scope_value(credential_id)
            and _scope_value(account_id, allow_empty=True)
            and type(verify_files) is bool and isinstance(library_root, Path)
            and type(staging_roots) is tuple
            and all(isinstance(p, Path) for p in staging_roots)):
        raise ValueError("invalid_audit_scope")
    salt = os.urandom(16) if opaque_salt is None else opaque_salt
    if type(salt) is not bytes or not 16 <= len(salt) <= 64:
        raise ValueError("invalid_audit_salt")

    with storage._lock, storage._connect() as db:
        # One SQLite snapshot; do not let a concurrent sync mix before/after.
        db.execute("BEGIN")
        binding = db.execute(
            "SELECT c.enabled, i.directory_name FROM provider_connections c "
            "JOIN identities i ON i.identity_id=c.identity_id "
            "JOIN provider_accounts a ON a.connection_id=c.connection_id "
            "WHERE c.identity_id=? AND c.provider_id=? AND c.credential_id=? "
            "AND a.identity_id=? AND a.provider_id=? AND a.credential_id=? "
            "AND a.account_id=?",
            (identity_id, provider_id, credential_id,
             identity_id, provider_id, credential_id, account_id),
        ).fetchall()
        if len(binding) != 1:
            raise ValueError("source_not_configured" if not binding else "account_ambiguous")
        enabled, owner_directory = binding[0]
        if enabled != 1:
            raise ValueError("source_disabled")
        if not _scope_value(owner_directory):
            raise ValueError("invalid_profile_directory")
        sources = db.execute(
            "SELECT source_reference_id,edition_id,right_id,reference_kind,state,"
            "synchronizable FROM source_references "
            "WHERE identity_id=? AND provider_id=? AND credential_id=? "
            "AND account_id=? ORDER BY source_reference_id LIMIT ?",
            (identity_id, provider_id, credential_id, account_id, _MAX_ROWS + 1),
        ).fetchall()
        if len(sources) > _MAX_ROWS:
            raise ValueError("audit_limit")
        source_ids = {r[0] for r in sources}
        links = db.execute(
            "SELECT o.source_reference_id,o.origin_link_id,o.artifact_id,"
            "a.sha256,a.size,p.placement_id,p.path,p.state "
            "FROM origin_links o "
            "JOIN source_references s ON s.source_reference_id=o.source_reference_id "
            "LEFT JOIN artifacts a ON a.artifact_id=o.artifact_id "
            "LEFT JOIN placements p ON p.artifact_id=o.artifact_id "
            "AND p.identity_id=o.identity_id "
            "WHERE o.origin_kind='provider' AND o.identity_id=? "
            "AND s.identity_id=? AND s.provider_id=? AND s.credential_id=? "
            "AND s.account_id=? LIMIT ?",
            (identity_id, identity_id, provider_id, credential_id, account_id,
             _MAX_ROWS + 1),
        ).fetchall()
        if len(links) > _MAX_ROWS:
            raise ValueError("audit_limit")
        local_cards = db.execute(
            "SELECT c.placement_id,c.local_state,c.provider_id,c.item_id "
            "FROM catalog_entries c JOIN placements p ON p.placement_id=c.placement_id "
            "WHERE c.identity_id=? AND p.identity_id=? LIMIT ?",
            (identity_id, identity_id, _MAX_ROWS + 1),
        ).fetchall()
        remote_cards = db.execute(
            "SELECT item_id FROM catalog_entries "
            "WHERE identity_id=? AND provider_id=? AND placement_id IS NULL "
            "AND local_state<>'local' LIMIT ?",
            (identity_id, provider_id, _MAX_ROWS + 1),
        ).fetchall()
        receipts = db.execute(
            "SELECT acquisition_id, status, artifact_id, attempt_count, error_code "
            "FROM acquisition_ledger WHERE identity_id=? AND provider_id=? "
            "AND credential_id=? AND account_id=? LIMIT ?",
            (identity_id, provider_id, credential_id, account_id, _MAX_ROWS + 1),
        ).fetchall()
    if (len(local_cards) > _MAX_ROWS or len(remote_cards) > _MAX_ROWS
            or len(receipts) > _MAX_ROWS):
        raise ValueError("audit_limit")
    cards = {row[0] for row in local_cards if row[1] == "local"}
    remote_items = {row[0] for row in remote_cards if type(row[0]) is str}
    scoped_links: dict[str, list[tuple]] = {}
    for row in links:
        if row[0] in source_ids:
            scoped_links.setdefault(row[0], []).append(row)

    items: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for source_id, edition, right, kind, state, can_sync in sources:
        grouped = scoped_links.get(source_id, [])
        has_origin = bool(grouped)
        final_verified = False
        final_unverified = False
        staging_found = False
        physical_found = False
        missing = False
        visible_staging = False
        linked_ui = False
        for _, _origin_id, _artifact_id, sha, size, placement_id, path, placement_state in grouped:
            if placement_id is None:
                continue
            zone, physical_path, anchor = _path_scope(
                path, library_root=library_root, owner_directory=owner_directory,
                staging_roots=staging_roots,
            )
            observed = _physical(
                physical_path, anchor, sha=sha, size=size,
                verify_files=verify_files,
            )
            exists = observed in ("verified", "present_unverified")
            physical_found |= exists
            missing |= observed == "missing"
            if zone == "final" and placement_state == "present":
                final_verified |= observed == "verified"
                final_unverified |= observed == "present_unverified"
                linked_ui |= placement_id in cards and exists
            if zone == "staging" and exists:
                staging_found = True
                visible_staging |= placement_id in cards
        remote_pair = right in remote_items or edition in remote_items
        separated = bool(remote_pair and (linked_ui or visible_staging))
        if final_verified:
            outcome = "complete_local"
        elif staging_found:
            outcome = "staged_unpublished"
        elif final_unverified:
            outcome = "published_unverified"
        elif has_origin and not any(r[5] for r in grouped):
            outcome = "receipt_without_placement"
        elif missing:
            outcome = "missing_file"
        elif not has_origin:
            outcome = "source_unlinked"
        else:
            outcome = "placement_unverified"
        counts[outcome] += 1
        if visible_staging:
            counts["staged_visible_in_library"] += 1
        if final_verified and linked_ui:
            counts["published_linked_to_origin"] += 1
        if separated:
            counts["separate_local_and_remote_cards"] += 1
        items.append({
            "source_ref": _opaque(salt, source_id), "kind": kind,
            "source_state": state, "synchronizable": can_sync == 1,
            "code": outcome, "local_artifact_found": physical_found,
            "origin_link_present": has_origin,
            "origin_link_account_match": has_origin,
            "published_final_placement": final_verified,
            "staging_only": staging_found and not final_verified,
            "ui_projection_linked": linked_ui,
            "staged_visible_in_library": visible_staging,
            "separate_local_and_remote_cards": separated,
            # Title/ISBN matching is never authoritative or attempted here.
            "match_basis": "origin_exact" if has_origin else "none",
        })
    safe_receipts = [{
        "receipt_ref": _opaque(salt, r[0]),
        "state": r[1] if r[1] in (
            "discovered", "pending", "acquiring", "complete",
            "retryable_error", "attention_required", "failed",
        ) else "unknown",
        "artifact_recorded": bool(r[2]),
        "attempt_count": r[3] if type(r[3]) is int and 0 <= r[3] <= 10_000 else None,
        "error_code": r[4] if r[4] in (
            "source_disabled", "operation_busy", "source_sync_partial",
            "http_error", "preflight_failed", "publication_failed",
        ) else "unclassified",
    } for r in receipts]
    return {
        "schema_version": 1, "scope": "exact_account",
        "snapshot_availability": "available",
        "scope_ref": _opaque(salt, (identity_id, provider_id, credential_id, account_id)),
        "provider": provider_id, "read_only": True, "network": False,
        "verify_files": verify_files, "source_count": len(items),
        "receipt_count": len(safe_receipts), "counts": dict(sorted(counts.items())),
        "sources": items, "receipts": safe_receipts,
        "coverage": {
            "source_references": "persisted_only",
            "staging": "configured_roots_only" if staging_roots else "unavailable",
            "filesystem": "sha_checked" if verify_files else "presence_only",
            "provider_inventory": "not_refreshed",
            "catalogue_matching": "exact_origin_only",
        },
    }
