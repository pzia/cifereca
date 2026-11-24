"""Local regression capture writer. Trusted Core supplies export-authorized bytes.

No network, no Storage access, no automatic authorization of raw input.
Call outside SQLite transactions. A missing member is explicitly partial.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

from ..install_paths import downloads_dir

_MEMBERS = ("response.raw", "baseline.json", "last_healthy.json",
            "current_parse.json", "diff.json", "environment.json")
_MAX_BYTES = 8 * 1024 * 1024


def write_regression_capture(*, diagnostic_id, generation, members, directory=None):
    """Write one immutable ZIP per diagnostic/generation, with private mode.

    Members must already be authorized and sanitized by the Core caller.
    Missing members are recorded in the manifest, never silently fabricated.
    """
    if (type(diagnostic_id) is not str
            or re.fullmatch(r"[0-9a-f]{64}", diagnostic_id) is None
            or type(generation) is not int or not 1 <= generation < 2**63
            or type(members) is not dict
            or any(type(k) is not str or k not in _MEMBERS
                   or type(v) is not bytes for k, v in members.items())
            or sum(map(len, members.values())) > _MAX_BYTES):
        raise ValueError("invalid_regression_capture")
    manifest = {
        "schema": 1, "diagnostic_id": diagnostic_id, "generation": generation,
        "status": "complete" if len(members) == len(_MEMBERS) else "partial",
        "omitted": [name for name in _MEMBERS if name not in members],
        "sha256": {name: hashlib.sha256(data).hexdigest()
                   for name, data in members.items()},
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    folder = downloads_dir() if directory is None else Path(directory)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / f"cifereca-regression-{diagnostic_id}-{generation}.zip"
    fd, temporary = tempfile.mkstemp(prefix=".cifereca-regression-", dir=folder)
    try:
        with os.fdopen(fd, "wb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", encoded)
                for name in _MEMBERS:
                    if name in members:
                        archive.writestr(name, members[name])
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic no-clobber publication, including concurrent identical alerts.
        try:
            os.link(temporary, target)
        except FileExistsError:
            # Never follow a pre-existing symlink or accept unrelated content.
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            with os.fdopen(os.open(target, flags), "rb") as existing:
                import stat
                info = os.fstat(existing.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                        or info.st_size > _MAX_BYTES + 65536):
                    raise ValueError("regression_capture_conflict")
                with zipfile.ZipFile(existing) as archive:
                    expected = {"manifest.json": encoded, **members}
                    if (sorted(archive.namelist()) != sorted(expected)
                            or any(archive.getinfo(name).file_size != len(data)
                                   or archive.read(name) != data
                                   for name, data in expected.items())):
                        raise ValueError("regression_capture_conflict")
        return target, manifest["status"]
    finally:
        os.unlink(temporary)
