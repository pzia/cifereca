"""#3882: exact-source, read-only canonical acquisition inspection.

No provider/network I/O, mutations, title, receipt, credential, filesystem path
or raw metadata is returned. Legacy title/SHA matching NEVER creates origins.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile

_MAX_ROWS = 512
_MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
_LEDGER_STATES = frozenset({
    "complete", "partial", "failed", "aborted", "cancelled", "pending",
    "canonical_receipt_staged", "staged", "skipped",
})
_RIGHT_TYPES = frozenset({"purchase", "loan", "notice", "internal"})
_RIGHT_STATES = frozenset({"active", "inactive", "unknown"})
_PLACEMENT_STATES = frozenset({"present", "missing", "dedup_cleanup_pending"})


def _reference(secret: bytes, category: str, value: str) -> str:
    return hmac.new(secret, (category + "\x00" + value).encode("utf-8"), hashlib.sha256).hexdigest()


def _file_proof(*, root: Path, directory: str, path: str, artifact_id: str) -> str:
    """Bounded SHA proof under the exact profile root, never follow symlinks."""
    if (type(directory) is not str or not directory or directory in (".", "..")
            or "/" in directory or "\\" in directory
            or type(path) is not str or not path
            or type(artifact_id) is not str or not artifact_id.startswith("sha256:")
            or len(artifact_id) != 71
            or any(c not in "0123456789abcdef" for c in artifact_id[7:])):
        return "invalid_placement"
    try:
        root_path = Path(os.path.abspath(root.expanduser()))
        physical = Path(os.path.abspath(Path(path).expanduser()))
        relative = physical.relative_to(root_path / directory)
        if not relative.parts or any(x in (".", "..") for x in relative.parts):
            return "invalid_placement"
        current = root_path
        for component in (directory, *relative.parts):
            current = current / component
            if current.is_symlink():
                return "invalid_placement"
        if not physical.exists():
            return "missing_file"
        if not physical.resolve(strict=True).is_relative_to(
            (root_path / directory).resolve(strict=True)
        ):
            return "invalid_placement"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(physical, flags)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                return "invalid_placement"
            if before.st_size > _MAX_FILE_BYTES or before.st_size <= 0:
                return "invalid_placement"
            digest = hashlib.sha256()
            while True:
                part = os.read(fd, 1024 * 1024)
                if not part:
                    break
                digest.update(part)
            after = os.fstat(fd)
            if any(getattr(before, key) != getattr(after, key)
                   for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")):
                return "file_changed"
            if digest.hexdigest() != artifact_id[7:]:
                return "sha_mismatch"
            return "verified_sha256"
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError, RuntimeError):
        return "file_unavailable"


def inspect_exact_source(
    storage, account, *, library_root: Path | None = None,
    verify_files: bool = False, pseudonym_key: bytes | None = None,
) -> dict[str, object]:
    """Offline inspection of up to 512 source rights and 512 receipts.

    Identity, provider, credential and account must match *exactly*. Rights
    without OriginLinks remain unlinked, never presumed acquired from a title
    or a coincidentally identical artifact digest.
    """
    if type(verify_files) is not bool:
        raise ValueError("invalid_acquisition_audit_options")
    if verify_files and not isinstance(library_root, Path):
        raise ValueError("acquisition_audit_library_root_required")
    secret = pseudonym_key if pseudonym_key is not None else secrets.token_bytes(32)
    if type(secret) is not bytes or len(secret) < 16:
        raise ValueError("invalid_acquisition_audit_pseudonym_key")
    coords = (account.identity_id, account.provider_id,
              account.credential_id, account.unique_id)
    from .acquisition_staging_evidence import receipt_staging_evidence
    with storage._lock, storage._connect() as db:
        refs = db.execute(
            "SELECT source_reference_id,reference_kind,state "
            "FROM source_references WHERE identity_id=? AND provider_id=? "
            "AND credential_id=? AND account_id=? ORDER BY source_reference_id LIMIT ?",
            (*coords, _MAX_ROWS + 1),
        ).fetchall()
        ledger = db.execute(
            "SELECT acquisition_id,status FROM acquisition_ledger "
            "WHERE identity_id=? AND provider_id=? AND credential_id=? "
            "AND account_id=? ORDER BY acquisition_id LIMIT ?",
            (*coords, _MAX_ROWS + 1),
        ).fetchall()
        links = {}
        for ref_id, _kind, _state in refs[:_MAX_ROWS]:
            links[ref_id] = db.execute(
                "SELECT o.artifact_id,p.placement_id,p.path,p.state "
                "FROM origin_links AS o LEFT JOIN placements AS p "
                "ON p.artifact_id=o.artifact_id AND p.identity_id=o.identity_id "
                "WHERE o.source_reference_id=? AND o.identity_id=?",
                (ref_id, coords[0]),
            ).fetchall()
        staging = receipt_staging_evidence(
            db, coords=coords,
            staging_root=(storage.path.parent / "acquisition-work")
            if isinstance(getattr(storage, "path", None), Path) else None,
            verify_files=verify_files,
            reference=lambda value: _reference(secret, "receipt", value),
        )
    directory = None
    if verify_files:
        try:
            matches = [r.get("directory_name") for r in storage.identities()
                       if r.get("identity_id") == coords[0]]
            if len(matches) == 1 and type(matches[0]) is str:
                directory = matches[0]
        except Exception:
            pass
    rights_out = []
    for ref_id, kind, state in refs[:_MAX_ROWS]:
        matched = links[ref_id]
        physical = {
            row[1]: (row[0], row[2], row[3]) for row in matched if row[1] is not None
        }
        if not matched:
            classification = "source_unlinked"
        elif not physical:
            classification = "source_linked_unplaced"
        elif len(physical) > 1:
            classification = "ambiguous_match"
        else:
            classification = "local_unverified"
        check = "not_requested"
        if physical and verify_files:
            artifact, path, placement_state = next(iter(physical.values()))
            if directory is None:
                check = "scope_unavailable"
            elif placement_state != "present":
                check = "missing_file"
            else:
                check = _file_proof(
                    root=library_root, directory=directory,
                    path=path, artifact_id=artifact,
                )
            if classification == "local_unverified":
                classification = "complete_local" if check == "verified_sha256" else (
                    "missing_file" if check == "missing_file" else "local_unverified"
                )
        rights_out.append({
            "resource_ref": _reference(secret, "right", ref_id),
            "kind": kind if kind in _RIGHT_TYPES else "other",
            "right_state": state if state in _RIGHT_STATES else "unknown",
            "classification": classification,
            "origin_link_count": min(len(matched), _MAX_ROWS),
            "placement_count": min(len(physical), _MAX_ROWS),
            "file_verification": check,
        })
    receipt_out = [{
        "receipt_ref": _reference(secret, "receipt", acquisition_id),
        "status": status if status in _LEDGER_STATES else "other",
        "classification": "staged_unpublished"
        if status == "canonical_receipt_staged" else "receipt_recorded",
    } for acquisition_id, status in ledger[:_MAX_ROWS]]
    return {
        "schema_version": 1, "scope": "exact_source",
        "network": "not_used", "mutations": "none",
        "verify_files": verify_files,
        "truncated": len(refs) > _MAX_ROWS or len(ledger) > _MAX_ROWS,
        "rights": rights_out, "receipts": receipt_out,
        "staging_evidence": staging,
    }



def write_acquisition_audit(destination: Path, data: dict[str, object]) -> None:
    """Atomic O_EXCL 0600 JSON publication, never replace an existing file."""
    if not isinstance(destination, Path):
        raise ValueError("acquisition_audit_invalid_output")
    target = Path(os.path.abspath(destination.expanduser()))
    parent = target.parent
    if (os.path.lexists(target) or not parent.is_dir()
            or any(p.is_symlink() for p in (target, *target.parents))):
        raise FileExistsError("acquisition_audit_destination_exists")
    payload = (json.dumps(data, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":")) + "\n").encode("ascii")
    if len(payload) > 512 * 1024:
        raise ValueError("acquisition_audit_size_limit")
    temp = None
    try:
        fd, path = tempfile.mkstemp(prefix=".cifereca-audit-", suffix=".tmp", dir=parent)
        temp = Path(path)
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, target, follow_symlinks=False)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
