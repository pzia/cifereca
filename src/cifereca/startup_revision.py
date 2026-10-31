"""Safe, optional deployed revision evidence for startup diagnostics."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

_REVISION = re.compile(r"[0-9a-fA-F]{40}")


_STORED_REVISION = "_build_revision.txt"


def stamp_source_git_commit(source: Path, commit: str) -> None:
    """Embed the verified checkout SHA in a temporary GitHub source tree.

    The installer calls this before building its offline wheel; the release
    workflow calls it before packaging. Never stamp dry-run deployments.
    """
    if (not isinstance(source, Path) or type(commit) is not str
            or _REVISION.fullmatch(commit) is None):
        raise ValueError("invalid_source_git_commit")
    package = source / "src" / "cifereca"
    marker = package / _STORED_REVISION
    if not package.is_dir() or package.is_symlink() or marker.is_symlink():
        raise ValueError("invalid_source_revision_target")
    marker.write_text(commit.lower() + "\n", encoding="ascii")


def _packaged_git_commit() -> str | None:
    """Read only a bounded, strictly validated package-local SHA marker."""
    try:
        with Path(__file__).with_name(_STORED_REVISION).open("rb") as stream:
            raw = stream.read(42)
        if len(raw) != 41 or not raw.endswith(b"\n"):
            return None
        commit = raw[:-1].decode("ascii")
        return commit.lower() if _REVISION.fullmatch(commit) else None
    except (OSError, UnicodeError, ValueError):
        return None


def startup_git_commit() -> str | None:
    """Prefer explicit deployment metadata, packaged SHA, then Git checkout.

    No repository path, stderr or Git configuration is logged. Source archives
    without a stamped marker or .git checkout report no Git revision.
    """
    configured = os.environ.get("CIFERECA_GIT_COMMIT", "").strip()
    if _REVISION.fullmatch(configured):
        return configured.lower()
    packaged = _packaged_git_commit()
    if packaged is not None:
        return packaged
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    revision = result.stdout.strip()
    return revision.lower() if result.returncode == 0 and _REVISION.fullmatch(revision) else None
