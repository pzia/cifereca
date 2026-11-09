"""Passive validation of unchanged local import bytes for a display-only link.

This check does NOT assign provider rights or attach a local artifact.
The caller owns any subsequent exact scoped SQLite/CAS binding. A valid EPUB
or PDF is not evidence of loan ownership, purchase, or ADEPT authorization.
"""
from __future__ import annotations

from pathlib import Path

from ..artifact_inspection import inspect_artifact
from ..library_files import sha256_file


def verified_manual_import_candidate(service, result: object):
    """Return (acquisition_id, path, artifact_id) only for unchanged bytes.

    No provider calls, writes or logging of private filenames. Hash and
    inspection must agree with the same physical file snapshot.
    """
    try:
        if (type(result) is not dict
                or result.get("status") not in {"complete", "unchanged"}
                or type(result.get("acquisition_id")) is not str
                or not result["acquisition_id"]
                or type(result.get("path")) is not str or not result["path"]):
            return None
        acquisition_id = result["acquisition_id"]
        row = service.storage.acquisition_get(acquisition_id)
        if (type(row) is not dict or type(row.get("artifact_id")) is not str
                or not row["artifact_id"].startswith("sha256:")):
            return None
        path = Path(result["path"])
        if path.is_symlink() or not path.is_file():
            return None
        before = path.stat(follow_symlinks=False)
        # Inspect and hash only local bytes; do not trust an upstream title,
        # remote URL or a declared identity as proof of unchanged content.
        if not inspect_artifact(path).valid:
            return None
        digest = sha256_file(path)
        after = path.stat(follow_symlinks=False)
        if (any(getattr(before, attr) != getattr(after, attr)
                for attr in ("st_dev", "st_ino", "st_size",
                             "st_mtime_ns", "st_ctime_ns"))
                or row["artifact_id"] != "sha256:" + digest):
            return None
        return acquisition_id, path, row["artifact_id"]
    except (OSError, ValueError, TypeError, KeyError):
        # The cause may contain user paths; never leak it to diagnostics.
        return None
