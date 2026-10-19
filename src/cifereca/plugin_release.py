"""Release-local expected plugin versions.

The manifest is shipped with the Core artifact so freshness checks are
reproducible offline and tied to the Cifereca release/snapshot.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Iterable

from packaging.version import InvalidVersion, Version


_MANIFEST = Path(__file__).with_name("plugin-release.json")


@lru_cache(maxsize=1)
def plugin_release_manifest() -> dict[str, object]:
    try:
        raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": 1, "release": "", "packages": {}}
    if not isinstance(raw, dict):
        return {"schema": 1, "release": "", "packages": {}}
    packages = raw.get("packages")
    if not isinstance(packages, dict):
        packages = {}
    return {
        "schema": int(raw.get("schema") or 1),
        "release": str(raw.get("release") or ""),
        "packages": packages,
    }


def expected_plugin_packages() -> dict[str, dict[str, str]]:
    raw = plugin_release_manifest().get("packages") or {}
    out: dict[str, dict[str, str]] = {}
    if not isinstance(raw, dict):
        return out
    for package_id, item in raw.items():
        if not isinstance(item, dict):
            continue
        version = str(item.get("version") or "").strip()
        if not package_id or not version:
            continue
        out[str(package_id)] = {
            "version": version,
            "name": str(item.get("name") or package_id),
        }
    return out


def classify_package_freshness(
    active: str | None,
    expected: str | None,
    installed_versions: Iterable[str] = (),
) -> tuple[str, str]:
    """Return canonical freshness status and a stable reason code."""
    versions = tuple(str(value) for value in installed_versions if str(value))
    if not expected:
        return "unknown", "unmanaged"
    if not active:
        return ("unknown", "inactive") if versions else ("unknown", "not_installed")
    try:
        active_version = Version(str(active))
        expected_version = Version(str(expected))
    except InvalidVersion:
        return "unknown", "invalid_version"
    if active_version == expected_version:
        return "up_to_date", ""
    if active_version < expected_version:
        return "outdated", "older_than_expected"
    return "newer_nonstandard", "newer_than_expected"


def package_freshness(
    package_id: str,
    active: str | None,
    installed_versions: Iterable[str] = (),
) -> dict[str, str | None]:
    expected = expected_plugin_packages().get(str(package_id), {}).get("version")
    status, reason = classify_package_freshness(active, expected, installed_versions)
    return {
        "expected_version": expected,
        "freshness": status,
        "freshness_reason": reason,
    }


def installed_plugin_update_receipt(
    package_root: Path, package_id: str, installed_versions: Iterable[str],
) -> dict[str, object] | None:
    """Read a bounded local receipt, not remote/authenticated release metadata.

    This records the installer operation and external digest comparison; it
    cannot attest the Cosign signature or that an already-running GUI has
    loaded the installed package.
    """
    import re

    path = package_root / ".cifereca-last-plugin-update.json"
    try:
        if package_root.is_symlink() or path.is_symlink():
            return None
        if not path.is_file() or path.stat().st_size > 2048:
            return None
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if type(receipt) is not dict or receipt.get("schema") != 1 or receipt.get("scope") != "plugins-only":
        return None
    if receipt.get("package_id") != package_id or receipt.get("version") not in tuple(installed_versions):
        return None
    for key, pattern in (
        ("version", r"[A-Za-z0-9._+-]{1,64}"),
        ("snapshot_id", r"[A-Za-z0-9._+-]{0,128}"),
        ("source_git_commit", r"(?:[a-f0-9]{7,40})?"),
        ("core_version_tested", r"[A-Za-z0-9._+-]{0,64}"),
        ("snapshot_sha256", r"[a-f0-9]{64}"),
        ("package_sha256", r"[a-f0-9]{64}"),
    ):
        value = receipt.get(key)
        if type(value) is not str or not re.fullmatch(pattern, value):
            return None
    if type(receipt.get("trusted_digest_verified")) is not bool:
        return None
    # Explicitly never infer a trusted signature from manifest-provided data.
    if receipt.get("signature_verified") is not False:
        return None
    return {key: receipt[key] for key in (
        "scope", "version", "snapshot_id", "source_git_commit",
        "core_version_tested", "snapshot_sha256", "package_sha256",
        "trusted_digest_verified", "signature_verified",
    )}
