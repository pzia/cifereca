from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_artifact_file(
    source: Path,
    destination: Path,
    *,
    overwrite: bool,
    accept_identical: bool = True,
) -> Path:
    """Publish a same-filesystem artifact file without exposing a partial target."""
    source = Path(source)
    destination = Path(destination)
    if not source.is_file() or source.is_symlink():
        raise ValueError("artifact source must be a regular file")
    destination.parent.mkdir(parents=True, exist_ok=True)

    if overwrite:
        os.replace(source, destination)
        return destination

    try:
        os.link(source, destination)
    except FileExistsError:
        if (
            accept_identical
            and destination.is_file()
            and not destination.is_symlink()
            and _sha256(destination) == _sha256(source)
        ):
            return destination
        raise
    return destination


def publish_artifact_content(destination: Path, content: bytes) -> Path:
    """Atomically publish in-memory artifact content to a destination path."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".part",
        dir=destination.parent,
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(bytes(content))
            handle.flush()
            os.fsync(handle.fileno())
        return publish_artifact_file(temporary, destination, overwrite=True)
    finally:
        temporary.unlink(missing_ok=True)
