from __future__ import annotations

import os
from pathlib import Path
import tempfile


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
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
