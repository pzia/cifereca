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
