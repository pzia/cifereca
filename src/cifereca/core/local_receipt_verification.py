"""Passive exact-scope verification of a local receipt and indexed placement.

A complete acquisition entry is only a hint: bytes, original SHA, same
identity/provider directory, and current placement must all agree.
This does not authorize acquisition, possession, ADEPT or a new loan.
"""
from __future__ import annotations

from pathlib import Path

from ..artifact_inspection import inspect_artifact
from ..library_files import sha256_file


def local_receipt_file_verified(
    service, *, identity_id: str, placement_provider_id: str,
    acquisition_provider_id: str, acquisition_credential_id: str,
    row: object, path: Path, on_read_failure=None,
) -> bool:
    """Return False on ambiguous evidence or any unsuccessful filesystem read."""
    try:
        if (type(identity_id) is not str or not identity_id
                or type(placement_provider_id) is not str or not placement_provider_id
                or type(acquisition_provider_id) is not str or not acquisition_provider_id
                or type(acquisition_credential_id) is not str or not acquisition_credential_id
                or type(row) is not dict
                or not isinstance(path, Path)
                or row.get("identity_id") != identity_id
                or row.get("provider_id") != acquisition_provider_id
                or row.get("credential_id") != acquisition_credential_id
                or type(row.get("artifact_id")) is not str):
            return False
        expected_id = row["artifact_id"]
        artifact = service.storage.artifact_get(expected_id)
        if (type(artifact) is not dict or type(artifact.get("sha256")) is not str
                or expected_id != "sha256:" + artifact["sha256"]
                or type(artifact.get("size")) is not int
                or artifact["size"] <= 0):
            return False
        root = service.library_identity_provider_dir(identity_id, placement_provider_id)
        if root.is_symlink() or path.is_symlink() or not path.is_file():
            return False
        relative = path.relative_to(root)
        if len(relative.parts) < 2:
            return False
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return False
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        linked = service.storage.placement_for_path(path)
        if (type(linked) is not dict
                or linked.get("state") != "present"
                or linked.get("identity_id") != identity_id
                or linked.get("provider_id") != placement_provider_id
                or linked.get("artifact_id") != expected_id):
            return False
        before = path.stat(follow_symlinks=False)
        if before.st_size != artifact["size"] or sha256_file(path) != artifact["sha256"]:
            return False
        inspected = inspect_artifact(path)
        after = path.stat(follow_symlinks=False)
        return bool(
            inspected.valid is True
            and inspected.media_format in {"epub", "pdf"}
            and all(getattr(before, key) == getattr(after, key)
                    for key in ("st_dev", "st_ino", "st_size",
                                "st_mtime_ns", "st_ctime_ns"))
        )
    except (OSError, ValueError, TypeError, KeyError):
        # An optional bounded caller-owned diagnostic never receives a cause.
        if callable(on_read_failure):
            try:
                on_read_failure()
            except Exception:
                pass
        return False
