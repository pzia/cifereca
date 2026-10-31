"""Deployed Core build identity; no network calls or live ref resolution (#3885)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from . import __version__

_REVISION = re.compile(r"[0-9a-fA-F]{40}")


def startup_git_commit() -> str | None:
    """Legacy optional source-tree hint (NOT an installed-build attestation).

    Kept for compatibility; build_identity() is authoritative for diagnostics.
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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
            return {}
        content = json.loads(path.read_text(encoding="utf-8"))
        return content if type(content) is dict else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _installed_receipt() -> dict[str, Any]:
    for parent in Path(__file__).resolve().parents:
        marker = parent / ".cifereca-install.json"
        if marker.is_file():
            return _read_json(marker)
    return {}


def build_identity() -> dict[str, Any]:
    """Identity of *running installed bits*, not mutable git HEAD at startup.

    Prefer the exact commit already recorded by the managed GitHub installer;
    otherwise the immutable wheel build receipt. Invalid or contradictory
    metadata fails closed. Missing provenance is explicitly unavailable.
    """
    identity: dict[str, Any] = {
        "schema_version": 1, "status": "unavailable", "commit_sha": None,
        "build_version": __version__, "build_timestamp": None,
        "distribution_mode": "unknown", "origin": "unavailable",
    }
    packaged = _read_json(Path(__file__).with_name("_build_identity.json"))
    packaged_commit: str | None = None
    if (
        packaged.get("schema_version") == 1
        and type(packaged.get("build_version")) is str
        and packaged["build_version"] == __version__
        and packaged.get("distribution_mode") == "wheel"
    ):
        identity["distribution_mode"] = "wheel"
        stamp = packaged.get("build_timestamp")
        if type(stamp) is str and len(stamp) <= 40 and stamp.endswith("+00:00"):
            identity["build_timestamp"] = stamp
        sha = packaged.get("commit_sha")
        if type(sha) is str and _REVISION.fullmatch(sha):
            if packaged.get("origin") == "git_clean_checkout":
                packaged_commit = sha.lower()
                identity.update(status="known", commit_sha=packaged_commit, origin="wheel_manifest")

    receipt = _installed_receipt()
    if receipt:
        provenance = receipt.get("provenance")
        if type(provenance) is dict:
            method = provenance.get("type")
            if method in ("github", "archive", "source"):
                identity["distribution_mode"] = "managed_" + method
            candidate = provenance.get("commit")
            if (
                method == "github" and receipt.get("version") == __version__
                and type(candidate) is str and _REVISION.fullmatch(candidate)
            ):
                selected = candidate.lower()
                # Contradictory proofs are never silently reconciled.
                if packaged_commit is not None and packaged_commit != selected:
                    identity.update(status="unavailable", commit_sha=None, origin="provenance_conflict")
                elif packaged and packaged_commit is None:
                    # An installed wheel explicitly reports an unverified
                    # source build: a GitHub ref alone cannot attest its bytes.
                    identity.update(status="unavailable", commit_sha=None, origin="wheel_provenance_unavailable")
                else:
                    # Compatibility for managed installations predating
                    # embedded wheel metadata: the updater selected this SHA.
                    identity.update(status="known", commit_sha=selected, origin="managed_github_receipt")
    return identity
