"""P0 #5069 B3: read-only, fail-closed inventory of installed plugin versions.

The package store contains executable code and user-specific history. Never
instantiate PackageStore (its constructor creates directories), never mutate
or delete packages, and never emit package IDs or paths in public results.
The cross-store dependency graph is *not* yet complete: no removal authorized.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any

from .updater_retention import RetentionObject, plan_retention


MAX_PACKAGES = 4096
MAX_VERSIONS_PER_PACKAGE = 256
MAX_METADATA_BYTES = 64 * 1024
HEX_SHA = re.compile(r"[a-fA-F0-9]{64}\Z")


def _identity(prefix: str, name: str) -> str:
    return prefix + ":" + sha256(name.encode("utf-8")).hexdigest()


def _read_json_nofollow(path: Path) -> dict[str, Any]:
    """Read one bounded regular file, refusing symlinks and hardlink aliases."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size > MAX_METADATA_BYTES):
            raise ValueError("unsafe_metadata")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(MAX_METADATA_BYTES + 1)
        if len(raw) > MAX_METADATA_BYTES:
            raise ValueError("oversized_metadata")
    finally:
        os.close(fd)
    value = json.loads(raw.decode("utf-8"))
    if type(value) is not dict:
        raise ValueError("invalid_metadata")
    return value


def _installed_at(value: Any) -> float | None:
    if type(value) is not str or not 15 <= len(value) <= 80:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        timestamp = parsed.timestamp()
        return timestamp if math.isfinite(timestamp) else None
    except (OverflowError, TypeError, ValueError):
        return None


def inspect_plugin_version_history(packages_root: Path, *, now: float) -> dict[str, Any]:
    """Inventory version generations per plugin package, not an apply permit.

    Validate the package manifest, updater-generated install receipt, and
    active/previous pointers. Missing proofs retain objects conservatively.
    Unlinked staging and cross-store transaction refs remain blockers.
    """
    blockers: set[str] = {"cross_store_references_unqualified"}
    objects: list[RetentionObject] = []
    packages_root = Path(packages_root)

    if packages_root.is_symlink() or (packages_root.exists() and not packages_root.is_dir()):
        return {
            "schema": "cifereca.updater.plugin-retention-inventory/1",
            "safe_to_apply": False,
            "coverage": [],
            "blockers": ["unsafe_plugin_store"],
            "plan": None,
        }
    if not packages_root.exists():
        plan = plan_retention([], now=now)
        return {
            "schema": "cifereca.updater.plugin-retention-inventory/1",
            "safe_to_apply": False,
            "coverage": ["plugin_versions"],
            "blockers": sorted(blockers),
            "plan": plan,
        }

    packages = sorted(packages_root.iterdir(), key=lambda item: item.name)
    if len(packages) > MAX_PACKAGES:
        return {
            "schema": "cifereca.updater.plugin-retention-inventory/1",
            "safe_to_apply": False,
            "coverage": [],
            "blockers": ["plugin_store_too_large"],
            "plan": None,
        }
    for package in packages:
        if package.is_symlink() or not package.is_dir():
            blockers.add("unsafe_plugin_package_entry")
            continue
        unit = _identity("package", package.name)
        pointer = package / "current.json"
        pointer_ok = not pointer.is_symlink()
        active: str | None = None
        previous: str | None = None
        if pointer.exists():
            try:
                record = _read_json_nofollow(pointer)
                values = [record.get("active"), record.get("previous")]
                if any(v is not None and (type(v) is not str or not v) for v in values):
                    raise ValueError("invalid_pointer")
                active, previous = values
            except (OSError, UnicodeError, ValueError):
                pointer_ok = False
                blockers.add("plugin_pointer_unreadable")
        else:
            # A package can be installed but inactive. Without a trustworthy
            # pointer, no version is eligible for removal.
            pointer_ok = False
            blockers.add("plugin_pointer_absent")

        entries = sorted(package.iterdir(), key=lambda item: item.name)
        if len(entries) > MAX_VERSIONS_PER_PACKAGE + 32:
            blockers.add("plugin_version_inventory_too_large")
            continue
        observed_versions: set[str] = set()
        for version_dir in entries:
            if version_dir.name == "current.json":
                continue
            if version_dir.is_symlink():
                blockers.add("unsafe_plugin_version_entry")
                continue
            if not version_dir.is_dir():
                blockers.add("unexpected_plugin_package_file")
                continue
            version = version_dir.name
            if version.startswith("."):
                # Hidden replace/staging directories may be needed after a
                # crash; never assume they are stale without their journal.
                blockers.add("unqualified_plugin_staging")
                continue
            observed_versions.add(version)
            receipt_ok = False
            installed_at: float | None = None
            try:
                marker = _read_json_nofollow(version_dir / ".cifereca-install.json")
                manifest = _read_json_nofollow(version_dir / "cifereca-plugin.json")
                identity = manifest.get("package")
                installed_at = _installed_at(marker.get("installed_at"))
                receipt_ok = (
                    type(identity) is dict
                    and identity.get("id") == package.name
                    and identity.get("version") == version
                    and type(marker.get("sha256")) is str
                    and HEX_SHA.fullmatch(marker["sha256"]) is not None
                    and installed_at is not None
                )
            except (OSError, UnicodeError, ValueError):
                pass
            if not receipt_ok:
                blockers.add("plugin_version_ownership_unqualified")
            key = _identity("plugin_version", package.name + "\0" + version)
            objects.append(RetentionObject(
                object_id=key,
                store="plugin_versions",
                unit=unit,
                generation=key,
                installed_at=installed_at,
                owned_by_updater=receipt_ok,
                active=pointer_ok and version == active,
                pinned=not pointer_ok or version in (active, previous),
            ))
        if pointer_ok and (
            (active is not None and active not in observed_versions)
            or (previous is not None and previous not in observed_versions)
        ):
            blockers.add("unresolved_plugin_pointer")
        if not observed_versions and pointer_ok and (active is not None or previous is not None):
            blockers.add("unresolved_plugin_pointer")

    plan = plan_retention(objects, now=now)
    return {
        "schema": "cifereca.updater.plugin-retention-inventory/1",
        "safe_to_apply": False,
        "coverage": ["plugin_versions"],
        "blockers": sorted(blockers | set(plan["blockers"])),
        "plan": plan,
    }


def inspect_updater_retention_readonly(
    runtime_root: Path, packages_root: Path, *, now: float,
    backups_root: Path | None = None,
    core_backups_root: Path | None = None,
) -> dict[str, Any]:
    """One redacted projection for the independently qualified store families.

    Do not flatten separate plans: transaction->plugin version dependencies
    have not yet been resolved, and concatenation would invent an eligible GC
    plan. This aggregate is useful for operator diagnosis, not for deletion.
    """
    from .updater_retention_inventory import inspect_core_transaction_history

    core = inspect_core_transaction_history(runtime_root, now=now)
    plugins = inspect_plugin_version_history(packages_root, now=now)
    from .updater_retention_cross_store import inspect_transaction_plugin_references
    cross_store = inspect_transaction_plugin_references(
        runtime_root, plugins.get("plan"),
    )
    backups = None
    if backups_root is not None:
        from .updater_retention_backups import inspect_dev_updater_backups
        backups = inspect_dev_updater_backups(backups_root, now=now)
    core_backups = None
    if core_backups_root is not None:
        from .updater_retention_core_backups import inspect_core_preactivation_backups
        core_backups = inspect_core_preactivation_backups(core_backups_root, now=now)
    from .updater_retention_staging import inspect_persistent_staging
    staging = inspect_persistent_staging(
        runtime_root, packages_root, core_backups_root=core_backups_root, now=now,
    )
    coverage = set(core["coverage"]) | set(plugins["coverage"]) | set(staging["coverage"])
    blockers = set(core["blockers"]) | set(plugins["blockers"]) | set(staging["blockers"])
    blockers.update(cross_store["blockers"])
    plans = {
        "core_transactions": core["plan"],
        "plugin_versions": plugins["plan"],
        "known_persistent_staging": staging["plan"],
    }
    pending = ["persistent_staging", "cross_store_reference_proofs"]
    if backups is not None:
        coverage.update(backups["coverage"])
        blockers.update(backups["blockers"])
        plans["updater_backups"] = backups["plan"]
        if not backups["coverage"]:
            pending.append("updater_backups")
    else:
        pending.append("updater_backups")
    # Distinct backup producers/ownership proofs require distinct plans:
    # never sum them or pretend the graph of rollback references is known.
    if core_backups is not None:
        coverage.update(core_backups["coverage"])
        blockers.update(core_backups["blockers"])
        plans["core_preactivation_backups"] = core_backups["plan"]
        if not core_backups["coverage"]:
            pending.append("core_preactivation_backups")
    else:
        pending.append("core_preactivation_backups")
    return {
        "schema": "cifereca.updater.retention-inventory-combined/1",
        "safe_to_apply": False,
        "coverage": sorted(coverage),
        "pending_stores": sorted(pending),
        "blockers": sorted(blockers | {"partial_store_coverage"}),
        "plans": plans,
        "cross_store_reference_inspection": cross_store,
    }
