"""#3882: exact-source, read-only canonical acquisition inspection.

No provider/network I/O, mutations, title, receipt, credential, filesystem path
or raw metadata is returned. Legacy title/SHA matching NEVER creates origins.
"""
from __future__ import annotations

import errno
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
    """Bounded SHA proof under the exact profile root using no-follow FDs.

    Checking Path.is_symlink() before os.open(path) is not sufficient:
    another process can substitute an ancestor between check and open.
    Walk every directory from '/' with O_DIRECTORY|O_NOFOLLOW and open the
    file relative to the pinned parent FD. Recheck path/inode bindings after
    hashing; a renamed source directory never becomes a positive proof.
    """
    if (type(directory) is not str or not directory or directory in (".", "..")
            or "/" in directory or "\\" in directory
            or type(path) is not str or not path
            or type(artifact_id) is not str or not artifact_id.startswith("sha256:")
            or len(artifact_id) != 71
            or any(c not in "0123456789abcdef" for c in artifact_id[7:])):
        return "invalid_placement"
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        return "invalid_placement"
    parent_fd = -1
    file_fd = -1
    try:
        root_path = Path(os.path.abspath(root.expanduser()))
        physical = Path(os.path.abspath(Path(path).expanduser()))
        relative = physical.relative_to(root_path / directory)
        if not relative.parts or any(x in (".", "..") for x in relative.parts):
            return "invalid_placement"
        dir_flags = (
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        file_flags = (
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        parent_fd = os.open("/", dir_flags)
        parents = []
        current = Path("/")
        for component in (*root_path.parts[1:], directory, *relative.parts[:-1]):
            next_fd = os.open(component, dir_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
            current = current / component
            pinned = os.fstat(parent_fd)
            if not stat.S_ISDIR(pinned.st_mode):
                return "invalid_placement"
            parents.append((current, pinned))
        file_fd = os.open(relative.parts[-1], file_flags, dir_fd=parent_fd)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            return "invalid_placement"
        if before.st_size > _MAX_FILE_BYTES or before.st_size <= 0:
            return "invalid_placement"
        digest = hashlib.sha256()
        total = 0
        while total < before.st_size:
            part = os.read(file_fd, min(1024 * 1024, before.st_size - total))
            if not part:
                break
            digest.update(part)
            total += len(part)
        after = os.fstat(file_fd)
        stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if total != before.st_size or any(
            getattr(before, key) != getattr(after, key) for key in stable
        ):
            return "file_changed"
        # Detect an ancestor rename or substitution after the FD walk.
        # Neither stat check is permission to reopen through these paths.
        try:
            for parent, pinned in parents:
                current_stat = os.stat(parent, follow_symlinks=False)
                if (not stat.S_ISDIR(current_stat.st_mode)
                        or (current_stat.st_dev, current_stat.st_ino)
                        != (pinned.st_dev, pinned.st_ino)):
                    return "file_changed"
            current_file = os.stat(physical, follow_symlinks=False)
            if (not stat.S_ISREG(current_file.st_mode)
                    or (current_file.st_dev, current_file.st_ino)
                    != (before.st_dev, before.st_ino)):
                return "file_changed"
        except OSError:
            return "file_changed"
        if digest.hexdigest() != artifact_id[7:]:
            return "sha_mismatch"
        return "verified_sha256"
    except FileNotFoundError:
        return "missing_file"
    except OSError as exc:
        # ELOOP for a linked file, ENOTDIR for a linked ancestor opened
        # with O_DIRECTORY|O_NOFOLLOW. Neither grants another owner proof.
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            return "invalid_placement"
        return "file_unavailable"
    except (ValueError, TypeError, RuntimeError):
        return "file_unavailable"
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if parent_fd >= 0:
            os.close(parent_fd)

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
        # Keep OriginLinks and Placements as two independently bounded
        # inventories. Their SQL join would multiply N origins by M copies.
        links = {}
        placements = {}
        for ref_id, _kind, _state in refs[:_MAX_ROWS]:
            origins = db.execute(
                "SELECT origin_link_id FROM origin_links "
                "WHERE source_reference_id=? AND identity_id=? "
                "ORDER BY origin_link_id LIMIT ?",
                (ref_id, coords[0], _MAX_ROWS + 1),
            ).fetchall()
            links[ref_id] = origins
            placements[ref_id] = db.execute(
                "SELECT p.placement_id,p.artifact_id,p.path,p.state "
                "FROM placements AS p WHERE p.identity_id=? AND EXISTS ("
                "SELECT 1 FROM origin_links AS o "
                "WHERE o.source_reference_id=? AND o.identity_id=p.identity_id "
                "AND o.artifact_id=p.artifact_id) "
                "ORDER BY p.placement_id LIMIT ?",
                (coords[0], ref_id, _MAX_ROWS + 1),
            ).fetchall() if origins else []
        staging = receipt_staging_evidence(
            db, coords=coords,
            staging_root=(storage.path.parent / "acquisition-work")
            if isinstance(getattr(storage, "path", None), Path) else None,
            verify_files=verify_files,
            reference=lambda value: _reference(secret, "receipt", value),
            canonical_root=library_root,
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
    related_truncated = False
    for ref_id, kind, state in refs[:_MAX_ROWS]:
        origin_count = len(links[ref_id])
        physical_rows = placements[ref_id]
        if origin_count > _MAX_ROWS or len(physical_rows) > _MAX_ROWS:
            related_truncated = True
        physical = {
            row[0]: (row[1], row[2], row[3])
            for row in physical_rows[:_MAX_ROWS]
        }
        if not origin_count:
            classification = "source_unlinked"
        elif not physical:
            classification = "source_linked_unplaced"
        elif len(physical) > 1:
            classification = "ambiguous_match"
        else:
            classification = "local_unverified"
        check = "not_requested"
        multi_proof = None
        if physical and verify_files:
            def physical_proof(artifact: str, path: str, state: str) -> str:
                # SQL state='missing' is an old observation, NOT proof that
                # a path remains physically absent. A file may have returned
                # after a move or mount recovery. Reprobe under the same
                # exact no-follow profile fence as any present Placement.
                observed = _file_proof(
                    root=library_root, directory=directory,
                    path=path, artifact_id=artifact,
                )
                if state != "present" and observed == "verified_sha256":
                    # Bytes have reappeared, but the old SQL state has NOT
                    # been repaired. Do not count this as an active Placement
                    # or let a read-only audit silently promote it.
                    return "placement_state_unverified"
                return observed
            if len(physical) > 1:
                # Every same-identity Placement must be checked: using the
                # first row falsely reports "missing_file" when another
                # Placement of this exact Artifact remains verified.
                # Preserve ambiguous_match: these are still distinct paths,
                # not permission to relink, discard or deduplicate.
                if directory is None or len(physical_rows) > _MAX_ROWS:
                    check = "multiple_placements_unverified"
                    multi_proof = (0, 0, len(physical))
                else:
                    proofs = [
                        physical_proof(artifact, path, placement_state)
                        for artifact, path, placement_state in physical.values()
                    ]
                    verified = sum(value == "verified_sha256" for value in proofs)
                    missing = sum(value == "missing_file" for value in proofs)
                    multi_proof = (verified, missing, len(proofs) - verified - missing)
                    check = (
                        "multiple_placements_all_verified" if verified == len(proofs)
                        else "multiple_placements_mixed" if verified
                        else "multiple_placements_none_verified"
                    )
            else:
                artifact, path, placement_state = next(iter(physical.values()))
                if directory is None:
                    check = "scope_unavailable"
                else:
                    check = physical_proof(artifact, path, placement_state)
                if classification == "local_unverified":
                    classification = "complete_local" if check == "verified_sha256" else (
                        "missing_file" if check == "missing_file" else "local_unverified"
                    )
        item = {
            "resource_ref": _reference(secret, "right", ref_id),
            "kind": kind if kind in _RIGHT_TYPES else "other",
            "right_state": state if state in _RIGHT_STATES else "unknown",
            "classification": classification,
            "origin_link_count": min(origin_count, _MAX_ROWS),
            "placement_count": min(len(physical), _MAX_ROWS),
            "file_verification": check,
        }
        if multi_proof is not None:
            item.update(zip(
                ("verified_placement_count", "missing_placement_count",
                 "unverified_placement_count"), multi_proof,
            ))
        rights_out.append(item)
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
        "truncated": (len(refs) > _MAX_ROWS or len(ledger) > _MAX_ROWS
                      or related_truncated),
        "rights": rights_out, "receipts": receipt_out,
        "staging_evidence": staging,
    }




def safe_acquisition_audit(value: object) -> dict[str, object]:
    """Closed export DTO shared by CLI stdout and the standalone audit JSON.

    Inspecting a source is deliberately provider-neutral. Any unexpected
    diagnostic/credential field introduced by a provider or future adapter
    must not make the shareable output less restrictive than the Sync ZIP.
    """
    if (type(value) is not dict or type(value.get("schema_version")) is not int
            or value.get("schema_version") != 1
            or value.get("scope") != "exact_source"):
        raise ValueError("acquisition_audit_invalid_result")
    # Keep the same bounded, canonical resource/receipt vocabulary used for
    # the matching before/after members of the #3882 Sync ZIP.
    from .sync_report import _safe_resource_evidence
    return _safe_resource_evidence(value)


def write_acquisition_audit(destination: Path, data: dict[str, object]) -> None:
    """Atomic O_EXCL 0600 JSON publication, never replace an existing file."""
    if not isinstance(destination, Path):
        raise ValueError("acquisition_audit_invalid_output")
    target = Path(os.path.abspath(destination.expanduser()))
    parent = target.parent
    if (os.path.lexists(target) or not parent.is_dir()
            or any(p.is_symlink() for p in (target, *target.parents))):
        raise FileExistsError("acquisition_audit_destination_exists")
    payload = (json.dumps(safe_acquisition_audit(data), ensure_ascii=True,
                          sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
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
