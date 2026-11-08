"""P0 #5069 B3: read-only, ownership-qualified plugin-version history.

A version directory alone is not proof of an updater-managed object. This
inventory deliberately produces no deletion authority: plugin code may hold
modules/resources across Core rollbacks, and transaction references remain
unqualified until their graph is made explicit.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from .updater_retention import RetentionObject, plan_retention


def _opaque(kind: str, *parts: str) -> str:
    digest = hashlib.sha256(
        ("\x00".join((kind, *parts))).encode("utf-8")
    ).hexdigest()
    return f"{kind}:{digest}"


def _read_small_json(path: Path, *, max_bytes: int = 32768) -> Any:
    # refuse links even if they appear to point to regular files
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("missing_or_unsafe_file")
    return json.loads(path.read_text(encoding="utf-8"))


def _timestamp(raw: Any) -> float | None:
    if type(raw) is not str:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        stamp = parsed.timestamp()
    except (OSError, OverflowError, ValueError):
        return None
    return stamp if math.isfinite(stamp) else None


def inspect_plugin_version_history(
    package_root: Path, *, now: float,
) -> dict[str, Any]:
    """Inspect installed versions per package, never activate, hash or delete.

    This is a *partial* inventory of updater-owned plugin versions. The full
    cross-store dependency graph is absent; safe_to_apply is always False.
    """
    blockers: set[str] = {
        "cross_store_references_unqualified",
        "plugin_lifecycle_not_serialized",
    }
    objects: list[RetentionObject] = []
    if package_root.is_symlink():
        return {
            "schema": "cifereca.updater.plugin-retention-inventory/1",
            "safe_to_apply": False,
            "blockers": ["unsafe_plugin_store"],
            "plan": None,
        }
    if package_root.is_dir():
        packages = sorted(package_root.iterdir(), key=lambda p: p.name)
        if len(packages) > 2048:
            blockers.add("plugin_package_inventory_too_large")
            packages = []
        for pkg in packages:
            if pkg.is_symlink() or not pkg.is_dir():
                blockers.add("unsafe_plugin_package")
                continue
            package_name = pkg.name
            package_id = _opaque("package", package_name)
            # The installer stores current.json at package root, and can
            # leave no pointer for a deliberately inactive package.
            active: str | None = None
            previous: str | None = None
            pointer = pkg / "current.json"
            if pointer.is_symlink():
                blockers.add("unsafe_plugin_pointer")
                pointer_valid = False
            elif pointer.exists():
                try:
                    data = _read_small_json(pointer)
                    if (type(data) is not dict
                            or any(type(data.get(key)) not in (str, type(None))
                                   for key in ("active", "previous"))):
                        raise ValueError("invalid_pointer")
                    active, previous = data.get("active"), data.get("previous")
                    pointer_valid = True
                except (OSError, ValueError, UnicodeError, TypeError):
                    blockers.add("invalid_plugin_pointer")
                    pointer_valid = False
            else:
                pointer_valid = True

            entries = sorted(pkg.iterdir(), key=lambda p: p.name)
            if len(entries) > 2048:
                blockers.add("plugin_version_inventory_too_large")
                continue
            discovered: set[str] = set()
            for version in entries:
                if version.name == "current.json":
                    continue
                if version.name.startswith("."):
                    # Installer scratch and replacement leftovers are not
                    # indiscriminately interpreted as version generations.
                    blockers.add("plugin_staging_unqualified")
                    continue
                if version.is_symlink() or not version.is_dir():
                    blockers.add("unsafe_plugin_version")
                    continue
                version_name = version.name
                # Non-conforming folder names are still protected; no
                # interpretations of a user's arbitrary path as an install.
                valid_name = bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!_+-]{0,127}", version_name))
                if not valid_name:
                    blockers.add("invalid_plugin_version_name")
                discovered.add(version_name)
                owned = False
                installed: float | None = None
                if valid_name:
                    try:
                        receipt = _read_small_json(version / ".cifereca-install.json")
                        manifest = _read_small_json(version / "cifereca-plugin.json", max_bytes=262144)
                        package = manifest.get("package") if type(manifest) is dict else None
                        if (type(receipt) is not dict
                                or type(package) is not dict
                                or package.get("id") != package_name
                                or package.get("version") != version_name
                                or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("sha256") or ""))):
                            raise ValueError("untrusted_version_receipt")
                        installed = _timestamp(receipt.get("installed_at"))
                        owned = installed is not None
                    except (OSError, ValueError, UnicodeError, TypeError):
                        pass
                if not owned:
                    blockers.add("plugin_version_ownership_unqualified")
                generation_id = _opaque("version", package_name, version_name)
                objects.append(RetentionObject(
                    object_id=generation_id,
                    store="plugin_versions",
                    unit=package_id,
                    generation=generation_id,
                    installed_at=installed,
                    owned_by_updater=owned,
                    active=(not pointer_valid or version_name == active),
                    pinned=(not pointer_valid or version_name == previous),
                ))

            # A dangling active/rollback pointer cannot be silently ignored
            # even when all discovered generations look individually valid.
            if ((active is not None and active not in discovered)
                    or (previous is not None and previous not in discovered)):
                blockers.add("unresolved_plugin_pointer")

    plan = plan_retention(objects, now=now)
    return {
        "schema": "cifereca.updater.plugin-retention-inventory/1",
        "safe_to_apply": False,
        "coverage": ["plugin_versions"],
        "blockers": sorted(blockers | set(plan["blockers"])),
        "plan": plan,
    }
