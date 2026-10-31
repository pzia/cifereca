"""Embed the exact clean Git source revision in built Core wheels (#3885).

The receipt is generated inside build_lib, never into the source checkout.
Source archives and dirty trees have deliberately unavailable provenance.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tomllib

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

_ROOT = Path(__file__).resolve().parent
_SHA = re.compile(r"[0-9a-f]{40}")


def _git_output(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(_ROOT), *args],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def source_commit() -> str | None:
    """Report a SHA only when the wheel's source is the clean checkout HEAD."""
    if not (_ROOT / ".git").exists():
        return None
    toplevel = _git_output("rev-parse", "--show-toplevel")
    if toplevel is None or Path(toplevel).resolve() != _ROOT:
        return None
    # A checkout with modified/untracked source would not match HEAD.
    status = _git_output("status", "--porcelain", "--untracked-files=normal")
    if status is None or status:
        return None
    sha = _git_output("rev-parse", "--verify", "HEAD")
    return sha if sha is not None and _SHA.fullmatch(sha) else None


def build_receipt() -> dict[str, object]:
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    sha = source_commit()
    return {
        "schema_version": 1,
        "commit_sha": sha,
        "build_version": str(project["version"]),
        "build_timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "distribution_mode": "wheel",
        "origin": "git_clean_checkout" if sha else "unavailable",
    }


class build_py(_build_py):
    def run(self):
        super().run()
        destination = Path(self.build_lib) / "cifereca" / "_build_identity.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(build_receipt(), sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    def get_outputs(self, include_bytecode=1):
        return super().get_outputs(include_bytecode=include_bytecode) + [
            str(Path(self.build_lib) / "cifereca" / "_build_identity.json"),
        ]


setup(cmdclass={"build_py": build_py})
