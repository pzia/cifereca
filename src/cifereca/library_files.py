from __future__ import annotations

import hashlib
import re
from pathlib import Path


def safe_library_component(value: str, fallback: str) -> str:
    """Return a filesystem-safe identity/provider path component."""
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-")
    return text or fallback


def sha256_file(path: Path) -> str:
    """Hash an existing local artifact, propagating filesystem errors."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def try_sha256_file(path: Path) -> str | None:
    """Hash an artifact when present/readable; return None on filesystem error."""
    try:
        return sha256_file(path)
    except OSError:
        return None
