"""Safe, optional deployed revision evidence for startup diagnostics."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

_REVISION = re.compile(r"[0-9a-fA-F]{40}")


def startup_git_commit() -> str | None:
    """Prefer explicit deployment metadata, then a local Git working tree.

    No repository path, stderr or Git configuration is logged. Source archives
    without a .git checkout simply report no Git revision.
    """
    configured = os.environ.get("CIFERECA_GIT_COMMIT", "").strip()
    if _REVISION.fullmatch(configured):
        return configured.lower()
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
