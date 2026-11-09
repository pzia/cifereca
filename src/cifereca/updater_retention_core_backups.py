"""P0 #5069 B5 — read-only inventory of Core pre-activation backups.

Only the updater-owned state/update-backups namespace is admissible. Content
may include SQLite/configuration and plugins: never read or enumerate payload
files; never delete, follow symlinks or traverse manual backup namespaces.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from .updater_retention import RetentionObject, plan_retention


_NAME = re.compile(r"(?P<stamp>[0-9]{8}T[0-9]{6}Z)-(?P<digest>[0-9a-f]{10})\Z")
_LIMIT = 4096
_MAX_RECEIPT_BYTES = 16 * 1024


def _opaque_id(name: str) -> str:
    return "core_preactivation_backup:" + sha256(name.encode("utf-8")).hexdigest()


def _created_at(raw: Any) -> float | None:
    if type(raw) is not str or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", raw):
        return None
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc,
        ).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _qualified_backup(backup: Path) -> tuple[bool, float | None]:
    """Attest the existing _backup_persistent_state producer's exact marker."""
    match = _NAME.fullmatch(backup.name)
    if match is None:
        return False, None
    marker = backup / "backup.json"
    fd = -1
    try:
        fd = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or not 0 < info.st_size <= _MAX_RECEIPT_BYTES):
                return False, None
            raw = stream.read(_MAX_RECEIPT_BYTES + 1)
        if len(raw) > _MAX_RECEIPT_BYTES:
            return False, None
        receipt = json.loads(raw.decode("utf-8"))
        if type(receipt) is not dict or set(receipt) != {"label", "created_at"}:
            return False, None
        label = receipt["label"]
        if type(label) is not str or not 1 <= len(label) <= 512:
            return False, None
        when = receipt["created_at"]
        if (when != match["stamp"]
                or sha256(label.encode("utf-8")).hexdigest()[:10] != match["digest"]):
            return False, None
        stamp = _created_at(when)
        return (stamp is not None, stamp)
    except (OSError, UnicodeError, ValueError, TypeError):
        return False, None
    finally:
        if fd >= 0:
            os.close(fd)


def inspect_core_preactivation_backups(backups_root: Path, *, now: float) -> dict[str, Any]:
    """Return a redacted plan, never an authorization for physical deletion."""
    blockers: set[str] = {"core_backup_dependencies_unqualified"}
    objects: list[RetentionObject] = []
    backups_root = Path(backups_root)
    if backups_root.is_symlink() or (backups_root.exists() and not backups_root.is_dir()):
        return {
            "schema": "cifereca.updater.core-backup-inventory/1",
            "safe_to_apply": False, "coverage": [],
            "blockers": ["unsafe_core_backup_root"], "plan": None,
        }
    if backups_root.is_dir():
        entries = sorted(backups_root.iterdir(), key=lambda entry: entry.name)
        if len(entries) > _LIMIT:
            return {
                "schema": "cifereca.updater.core-backup-inventory/1",
                "safe_to_apply": False, "coverage": [],
                "blockers": ["core_backup_inventory_too_large"], "plan": None,
            }
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir():
                blockers.add("unsafe_core_backup_entry")
                continue
            if entry.name.startswith("."):
                blockers.add("core_backup_staging_unqualified")
                continue
            owned, timestamp = _qualified_backup(entry)
            if not owned:
                blockers.add("core_backup_ownership_unqualified")
            opaque = _opaque_id(entry.name)
            objects.append(RetentionObject(
                object_id=opaque,
                store="updater_backups",
                unit="core_pre_activation",
                generation=opaque,
                installed_at=timestamp,
                owned_by_updater=owned,
                pinned=not owned,
            ))
    plan = plan_retention(objects, now=now)
    return {
        "schema": "cifereca.updater.core-backup-inventory/1",
        "safe_to_apply": False,
        "coverage": ["updater_backups"],
        "blockers": sorted(blockers | set(plan["blockers"])),
        "plan": plan,
    }
