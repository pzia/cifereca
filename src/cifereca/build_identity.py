"""Bounded, non-sensitive build identity for startup and support diagnostics."""
from __future__ import annotations

import json
from pathlib import Path

from . import __version__
from .startup_revision import startup_git_commit


def _distribution_mode() -> str:
    """Recognize only local installation metadata and safe distribution labels."""
    location = Path(__file__).resolve()
    for parent in location.parents:
        marker = parent / ".cifereca-install.json"
        try:
            if not marker.is_file() or marker.is_symlink() or marker.stat().st_size > 16384:
                continue
            payload = json.loads(marker.read_text(encoding="utf-8"))
            provenance = payload.get("provenance") if type(payload) is dict else None
            label = provenance.get("type") if type(provenance) is dict else None
            return label if label in {"github", "archive", "package", "source"} else "managed"
        except (OSError, ValueError, TypeError, RecursionError):
            return "unknown"
    if location.with_name("_build_revision.txt").is_file():
        return "wheel"
    if (location.parents[2] / ".git").exists():
        return "checkout"
    return "unknown"


def build_identity() -> dict[str, str | None | int]:
    """No network, private paths, credentials or guessed commit IDs."""
    sha = startup_git_commit()
    return {
        "schema_version": 1,
        "build_version": __version__,
        "commit_sha": sha,
        "build_timestamp": None,  # Filled only by a future attested build manifest.
        "distribution_mode": _distribution_mode(),
        "availability": "known" if sha is not None else "unavailable",
    }
