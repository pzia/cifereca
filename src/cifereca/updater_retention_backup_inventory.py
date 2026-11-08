"""P0 #5069 B5 — read-only inventory of updater-specific backups.

Only the *explicit* updater-owned namespaces below are inspected. Never walk
arbitrary state directories, library files, manual backups or profile stores.
Even a qualified receipt grants no deletion authority while transactions and
snapshots can still reference the backup.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import stat
from pathlib import Path
import re
from typing import Any

from .updater_retention import RetentionObject, plan_retention


_BACKUP_NAME = re.compile(r"(?P<date>[0-9]{8}T[0-9]{6}Z)-(?P<digest>[a-f0-9]{10})\Z")


def _closed_id(category: str, name: str) -> str:
    return f"backup:{hashlib.sha256((category + chr(0) + name).encode()).hexdigest()}"


def _timestamp(raw: Any) -> float | None:
    if type(raw) is not str or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", raw):
        return None
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc,
        ).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _qualified_core_backup(backup: Path) -> tuple[bool, float | None]:
    """Only the original pre-activation producer contract is admissible."""
    matched = _BACKUP_NAME.fullmatch(backup.name)
    if matched is None:
        return False, None
    marker = backup / "backup.json"
    if marker.is_symlink():
        return False, None
    try:
        # The same descriptor must be used for checks and reads, otherwise
        # a symlink swap can escape the updater-owned backup namespace.
        fd = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or not 0 < info.st_size <= 16384):
                return False, None
            raw = stream.read(16385)
            if len(raw) > 16384:
                return False, None
        receipt = json.loads(raw.decode("utf-8"))
        if type(receipt) is not dict or set(receipt) != {"label", "created_at"}:
            return False, None
        label = receipt["label"]
        if type(label) is not str or not 1 <= len(label) <= 512:
            return False, None
        if (receipt["created_at"] != matched["date"]
                or hashlib.sha256(label.encode("utf-8")).hexdigest()[:10] != matched["digest"]):
            return False, None
        instant = _timestamp(receipt["created_at"])
        return (instant is not None, instant)
    except (OSError, ValueError, UnicodeError, TypeError):
        return False, None


def inspect_updater_backup_history(
    state_root: Path, *, now: float,
) -> dict[str, Any]:
    """Inventory known updater backup locations without any write or delete."""
    namespaces = (
        ("update-backups", "core_pre_activation"),
        ("dev-updates", "dev_snapshot"),
    )
    blockers: set[str] = {"backup_reference_graph_unqualified"}
    objects: list[RetentionObject] = []
    if state_root.is_symlink():
        return {
            "schema": "cifereca.updater.backup-retention-inventory/1",
            "safe_to_apply": False,
            "blockers": ["unsafe_updater_state_root"], "plan": None,
        }
    for store_name, unit in namespaces:
        root = state_root / store_name
        if root.is_symlink():
            blockers.add("unsafe_updater_backup_store")
            continue
        if not root.is_dir():
            continue
        entries = sorted(root.iterdir(), key=lambda p: p.name)
        if len(entries) > 4096:
            blockers.add("updater_backup_store_too_large")
            continue
        for directory in entries:
            if directory.is_symlink() or not directory.is_dir():
                blockers.add("unsafe_updater_backup_entry")
                continue
            if directory.name.startswith("."):
                # Crashed scratch folders belong to a distinct crash-safe
                # staging collector; not a version or backup generation.
                blockers.add("updater_backup_staging_unqualified")
                continue
            owned, when = (
                _qualified_core_backup(directory)
                if store_name == "update-backups" else (False, None)
            )
            if not owned:
                # dev-updates are produced without a durable receipt today:
                # their names and mtimes alone are NOT ownership proof.
                blockers.add("updater_backup_ownership_unqualified")
            opaque = _closed_id(store_name, directory.name)
            objects.append(RetentionObject(
                object_id=opaque,
                store="updater_backups",
                unit=unit,
                generation=opaque,
                installed_at=when,
                owned_by_updater=owned,
                pinned=not owned,
            ))
    policy = plan_retention(objects, now=now)
    return {
        "schema": "cifereca.updater.backup-retention-inventory/1",
        "coverage": ["updater_backups"],
        "safe_to_apply": False,
        "blockers": sorted(blockers | set(policy["blockers"])),
        "plan": policy,
    }
