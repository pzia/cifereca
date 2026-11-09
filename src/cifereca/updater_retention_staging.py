"""P0 #5069 B7: opaque, non-destructive inventory of persistent staging.

Never reads staging payloads, treats a name/mtime as proof of updater ownership,
or authorizes cleanup. Only bounded, shallow known updater namespaces are read.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
from typing import Any

from .updater_retention import RetentionObject, plan_retention


_TX_STAGE = re.compile(r"\.[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}\.[0-9]+\.tmp\Z")
_CORE_BACKUP_STAGE = re.compile(r"\.[0-9]{8}T[0-9]{6}Z-[a-f0-9]{10}\.[0-9]+\.tmp\Z")
_PLUGIN_STAGE = re.compile(r"\.[A-Za-z0-9][A-Za-z0-9.!_+-]{0,127}-[A-Za-z0-9_]{8}\Z")
_LIMIT = 4096


def _opaque(kind: str, *names: str) -> str:
    return "staging:" + sha256(
        ("\0".join((kind, *names))).encode("utf-8")
    ).hexdigest()


def _root_safe(root: Path) -> bool:
    # Do not resolve a path through a redirected user/supervisor namespace.
    return not any(node.is_symlink() for node in (root, *root.parents))


def inspect_persistent_staging(
    runtime_root: Path, package_root: Path, *,
    core_backups_root: Path | None = None,
    now: float,
) -> dict[str, Any]:
    """Inspect known staging envelopes only; every object remains pinned."""
    objects: list[RetentionObject] = []
    blockers: set[str] = {"staging_references_unqualified"}
    coverage: set[str] = set()

    def stage(kind: str, namespace: str, name: str) -> None:
        opaque = _opaque(kind, namespace, name)
        objects.append(RetentionObject(
            object_id=opaque, store="persistent_staging",
            unit=_opaque("unit", kind, namespace), generation=opaque,
            installed_at=None, owned_by_updater=False, pinned=True,
        ))
        blockers.add("persistent_staging_unqualified")

    def inspect_shallow(
        root: Path, *, kind: str, pattern: re.Pattern[str],
        namespace: str,
    ) -> None:
        if not _root_safe(root) or (root.exists() and not root.is_dir()):
            blockers.add("unsafe_staging_store")
            return
        if not root.exists():
            coverage.add(kind)
            return
        try:
            entries = sorted(root.iterdir(), key=lambda e: e.name)
        except OSError:
            blockers.add("staging_store_unreadable")
            return
        if len(entries) > _LIMIT:
            blockers.add("staging_inventory_too_large")
            return
        coverage.add(kind)
        for entry in entries:
            if not entry.name.startswith("."):
                continue
            if not pattern.fullmatch(entry.name):
                # Unknown hidden file/folder is not an owned cleanup candidate.
                blockers.add("unknown_hidden_staging")
                continue
            if entry.is_symlink() or not entry.is_dir():
                blockers.add("unsafe_staging_entry")
                continue
            stage(kind, namespace, entry.name)

    inspect_shallow(
        Path(runtime_root) / "transactions",
        kind="core_transaction_staging",
        pattern=_TX_STAGE, namespace="transactions",
    )
    if core_backups_root is not None:
        inspect_shallow(
            Path(core_backups_root), kind="core_backup_staging",
            pattern=_CORE_BACKUP_STAGE, namespace="core-backups",
        )
    packages = Path(package_root)
    if not _root_safe(packages) or (packages.exists() and not packages.is_dir()):
        blockers.add("unsafe_plugin_staging_store")
    elif packages.exists():
        try:
            parents = sorted(packages.iterdir(), key=lambda p: p.name)
        except OSError:
            parents = []
            blockers.add("plugin_staging_store_unreadable")
        if len(parents) > _LIMIT:
            blockers.add("plugin_staging_inventory_too_large")
        else:
            coverage.add("plugin_install_staging")
            for package in parents:
                if package.is_symlink() or not package.is_dir():
                    blockers.add("unsafe_plugin_staging_package")
                    continue
                # An installed package is a separate retention unit. Its
                # children may contain user data: inspect names/types only.
                inspect_shallow(
                    package, kind="plugin_install_staging",
                    pattern=_PLUGIN_STAGE,
                    namespace=_opaque("package", package.name),
                )
    else:
        coverage.add("plugin_install_staging")

    plan = plan_retention(objects, now=now)
    return {
        "schema": "cifereca.updater.staging-inventory/1",
        "safe_to_apply": False,
        "coverage": sorted(coverage),
        "blockers": sorted(blockers | set(plan["blockers"])),
        "plan": plan,
    }
