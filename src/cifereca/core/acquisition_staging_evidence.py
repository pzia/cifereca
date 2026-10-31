"""Positive staging evidence for exact-account acquisition receipts (#3882).

Never assigns provider ownership from an Artifact SHA or catalogue title.
No scan, mutation, network, repair, or inference from absent rows.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Any

_MAX_ROWS = 512
_MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024


def _verify_staged(path_value: Any, root: Path, sha: str, size: int) -> str:
    """Hash an indexed staging file without following symlinks."""
    if (type(path_value) is not str or type(sha) is not str
            or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)
            or type(size) is not int or not 0 < size <= _MAX_FILE_BYTES):
        return "unavailable"
    try:
        p = Path(path_value)
        if not p.is_absolute() or ".." in p.parts or not root.is_absolute():
            return "outside_staging"
        anchor = Path(os.path.abspath(root))
        path = Path(os.path.abspath(p))
        relative = path.relative_to(anchor)
        if not relative.parts:
            return "outside_staging"
        current = anchor
        if current.is_symlink():
            return "unsafe_path"
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return "unsafe_path"
        # A missing staging root never justifies absence of the provider book.
        if not anchor.is_dir():
            return "unavailable"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size != size:
                return "size_mismatch"
            digest = hashlib.sha256()
            while True:
                block = os.read(fd, 1024 * 1024)
                if not block:
                    break
                digest.update(block)
            after = os.fstat(fd)
            named = os.stat(path, follow_symlinks=False)
            for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"):
                if getattr(before, key) != getattr(after, key) or getattr(before, key) != getattr(named, key):
                    return "changed_during_verification"
            return "verified_sha256" if digest.hexdigest() == sha else "sha_mismatch"
        finally:
            os.close(fd)
    except FileNotFoundError:
        return "missing"
    except (OSError, ValueError, TypeError, RuntimeError):
        return "unavailable"


def receipt_staging_evidence(
    db: Any, *, coords: tuple[str, str, str, str], staging_root: Path | None,
    verify_files: bool, reference,
) -> dict[str, Any]:
    """Collect receipt-local staging observations from a single SQLite snapshot.

    Only receipts whose Artifact and Placement are already indexed can be
    inspected. A shared Artifact is not an OriginLink proof for this receipt.
    """
    if type(verify_files) is not bool or not callable(reference):
        raise ValueError("invalid_receipt_staging_audit")
    rows = db.execute(
        "SELECT a.acquisition_id,a.artifact_id,p.placement_id,p.path,p.state,"
        "art.sha256,art.size, "
        "EXISTS(SELECT 1 FROM catalog_entries c "
        "WHERE c.placement_id=p.placement_id AND c.identity_id=a.identity_id "
        "AND c.local_state='local') "
        "FROM acquisition_ledger a "
        "LEFT JOIN artifacts art ON art.artifact_id=a.artifact_id "
        "LEFT JOIN placements p ON p.artifact_id=a.artifact_id AND p.identity_id=a.identity_id "
        "WHERE a.identity_id=? AND a.provider_id=? AND a.credential_id=? "
        "AND a.account_id=? ORDER BY a.acquisition_id,p.placement_id LIMIT ?",
        (*coords, _MAX_ROWS + 1),
    ).fetchall()
    if len(rows) > _MAX_ROWS:
        return {"coverage": "truncated", "staged_visible_in_library": 0,
                "staged_verified": 0, "observations": []}
    root = staging_root
    output: dict[str, dict[str, Any]] = {}
    for receipt_id, artifact_id, placement_id, path, placement_state, sha, size, card in rows:
        entry = output.setdefault(receipt_id, {
            "receipt_ref": reference(receipt_id), "staging_state": "not_attested",
            "staged_visible_in_library": False,
            "staging_sha_verified": False,
            "origin_proven_by_receipt": False,
        })
        if (root is None or type(path) is not str or placement_id is None
                or type(artifact_id) is not str or placement_state != "present"):
            continue
        try:
            candidate = Path(os.path.abspath(path))
            root_alias = Path(os.path.abspath(root))
            rel = candidate.relative_to(root_alias)
            if not rel.parts or ".." in Path(path).parts or not Path(path).is_absolute():
                continue
        except (OSError, ValueError):
            continue
        if not verify_files:
            if entry["staging_state"] == "not_attested":
                entry["staging_state"] = "indexed_staging_unverified"
            continue
        state = _verify_staged(path, root_alias, sha, size)
        if state == "verified_sha256":
            entry["staging_state"] = "verified_staging_file"
            entry["staging_sha_verified"] = True
            entry["staged_visible_in_library"] |= bool(card)
        elif entry["staging_state"] not in ("verified_staging_file",):
            entry["staging_state"] = (
                "staging_file_missing" if state == "missing"
                else "staging_unavailable"
            )
    observations = list(output.values())
    return {
        "coverage": "indexed_account_receipts_only" if root is not None else "unavailable",
        "staged_visible_in_library": sum(r["staged_visible_in_library"] for r in observations),
        "staged_verified": sum(r["staging_sha_verified"] for r in observations),
        "observations": observations,
    }
