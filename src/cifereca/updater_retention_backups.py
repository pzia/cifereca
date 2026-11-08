"""P0 #5069 B4: durable DevUpdater backup ownership, read-only retention.

Legacy backups without a signed-off updater creation receipt remain *unowned*,
never removal candidates. This module never deletes files or user state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any

from .updater_retention import RetentionObject, plan_retention


_SCHEMA = "cifereca.updater.dev-backup-receipt/1"
_NAME = re.compile(r"[0-9a-f]{16}-[0-9]{12,21}\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_LIMIT = 20_000
_MAX_RECEIPT = 16 * 1024


def _id(name: str) -> str:
    return "dev_backup:" + sha256(name.encode("utf-8")).hexdigest()


def _backed_up_names(plugins: Path) -> list[str]:
    if plugins.is_symlink() or not plugins.is_dir():
        raise ValueError("backup_plugins_unsafe")
    children = list(plugins.iterdir())
    if len(children) > _LIMIT:
        raise ValueError("backup_plugins_too_large")
    names: list[str] = []
    for child in children:
        if child.is_symlink() or not child.is_dir():
            raise ValueError("backup_plugin_entry_unsafe")
        names.append(child.name)
    return sorted(names)


def _names_digest(names: list[str]) -> str:
    return sha256("\0".join(names).encode("utf-8")).hexdigest()


def _timestamp(raw: Any) -> float | None:
    if type(raw) is not str or not 15 <= len(raw) <= 80:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None or dt.utcoffset() is None:
            return None
        ts = dt.timestamp()
        return ts if math.isfinite(ts) else None
    except (OverflowError, TypeError, ValueError):
        return None


def write_dev_backup_receipt(backup_dir: Path, *, snapshot_sha256: str) -> None:
    """Commit updater ownership only after plugin copies have finished.

    Crash before rename: a partial backup is unqualified and retained. The
    receipt contains no package IDs, source paths or other user data.
    """
    if (type(snapshot_sha256) is not str
            or _DIGEST.fullmatch(snapshot_sha256) is None):
        raise ValueError("backup_snapshot_sha256_invalid")
    if backup_dir.is_symlink() or not backup_dir.is_dir():
        raise ValueError("backup_directory_unsafe")
    info = backup_dir.stat()
    if (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError("backup_directory_permissions_unsafe")
    names = _backed_up_names(backup_dir / "plugins")
    receipt = {
        "schema": _SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_sha256": snapshot_sha256,
        "package_count": len(names),
        "backed_up_packages_sha256": _names_digest(names),
    }
    target = backup_dir / ".cifereca-updater-owned.json"
    # O_EXCL forbids replacing another receipt. A crash during creation
    # leaves a partial/invalid receipt that the inventory refuses to own.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write((json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    directory_fd = os.open(
        backup_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_receipt(directory: Path) -> dict[str, Any]:
    receipt = directory / ".cifereca-updater-owned.json"
    fd = os.open(receipt, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > _MAX_RECEIPT):
            raise ValueError("backup_receipt_unsafe")
        raw = os.read(fd, _MAX_RECEIPT + 1)
        if len(raw) > _MAX_RECEIPT:
            raise ValueError("backup_receipt_too_large")
    finally:
        os.close(fd)
    record = json.loads(raw.decode("utf-8"))
    if type(record) is not dict:
        raise ValueError("backup_receipt_invalid")
    return record


def inspect_dev_updater_backups(backups_root: Path, *, now: float) -> dict[str, Any]:
    """Conservatively qualify DevUpdater backups, never enable a GC action."""
    blockers: set[str] = {"cross_store_backup_dependencies_unqualified"}
    objects: list[RetentionObject] = []
    backups_root = Path(backups_root)
    if backups_root.is_symlink() or (backups_root.exists() and not backups_root.is_dir()):
        return {
            "schema": "cifereca.updater.dev-backup-inventory/1",
            "safe_to_apply": False, "coverage": [],
            "blockers": ["unsafe_backups_root"], "plan": None,
        }
    if backups_root.is_dir():
        entries = sorted(backups_root.iterdir(), key=lambda item: item.name)
        if len(entries) > _LIMIT:
            return {
                "schema": "cifereca.updater.dev-backup-inventory/1",
                "safe_to_apply": False, "coverage": [],
                "blockers": ["backup_inventory_too_large"], "plan": None,
            }
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                blockers.add("unsafe_backup_entry")
                continue
            owned = False
            installed_at: float | None = None
            if _NAME.fullmatch(entry.name) is None:
                blockers.add("backup_name_unqualified")
            else:
                try:
                    if entry.stat().st_uid != os.getuid() or entry.stat().st_mode & 0o077:
                        raise ValueError("backup_dir_permissions")
                    receipt = _read_receipt(entry)
                    names = _backed_up_names(entry / "plugins")
                    installed_at = _timestamp(receipt.get("created_at"))
                    owned = (
                        receipt.get("schema") == _SCHEMA
                        and type(receipt.get("package_count")) is int
                        and receipt["package_count"] == len(names)
                        and type(receipt.get("snapshot_sha256")) is str
                        and _DIGEST.fullmatch(receipt["snapshot_sha256"]) is not None
                        and receipt.get("backed_up_packages_sha256") == _names_digest(names)
                        and installed_at is not None
                    )
                except (OSError, UnicodeError, ValueError):
                    pass
                if not owned:
                    blockers.add("backup_ownership_unqualified")
            object_id = _id(entry.name)
            objects.append(RetentionObject(
                object_id=object_id,
                store="updater_backups", unit="dev-updates",
                generation=object_id, installed_at=installed_at,
                owned_by_updater=owned,
                pinned=not owned,
            ))
    plan = plan_retention(objects, now=now)
    return {
        "schema": "cifereca.updater.dev-backup-inventory/1",
        "safe_to_apply": False,
        "coverage": ["updater_backups"],
        "blockers": sorted(blockers | set(plan["blockers"])),
        "plan": plan,
    }
