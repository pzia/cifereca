"""P0 #5069: physically reclaim attested Core pre-activation backups.

The producer-owned update-backups namespace is distinct from manual backups.
The shared inventory selects the 24-hour / minimum-two candidate set.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time

from .updater_retention_core_backups import (
    _NAME, _opaque_id, _qualified_backup, inspect_core_preactivation_backups,
)

_SCHEMA = "cifereca.updater.core-backup-gc/1"
_SHA = re.compile(r"[a-f0-9]{64}\Z")


def _sync(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY |
                 getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _plan(runtime: Path, backups: Path, now: float) -> tuple[list[tuple[Path, str]], str]:
    # A restoration-incomplete transaction may still need its pre-activation
    # backup. Do not reclaim while any such transaction exists.
    from .updater_dev_backup_maintenance import _no_incomplete_transaction
    _no_incomplete_transaction(runtime)
    report = inspect_core_preactivation_backups(backups, now=now)
    plan = report.get("plan")
    if (set(report.get("blockers", ())) - {
            "core_backup_dependencies_unqualified",
            "inventory_only_no_gc_implementation",
        } or type(plan) is not dict
            or type(plan.get("objects")) is not list
            or type(plan.get("candidates")) is not int):
        raise ValueError("core_backup_gc_inventory_unqualified")
    candidate_ids = {
        row["object_id"] for row in plan["objects"]
        if type(row) is dict and row.get("decision") == "candidate"
    }
    if len(candidate_ids) != plan["candidates"]:
        raise ValueError("core_backup_gc_plan_inconsistent")
    if backups.is_symlink() or backups.exists() and not backups.is_dir():
        raise ValueError("core_backup_gc_root_unsafe")
    selected = []
    for entry in sorted(backups.iterdir()) if backups.is_dir() else ():
        if entry.is_symlink() or not entry.is_dir():
            raise ValueError("core_backup_gc_unsafe_entry")
        object_id = _opaque_id(entry.name)
        if object_id in candidate_ids:
            owned, _ = _qualified_backup(entry)
            if not owned:
                raise ValueError("core_backup_gc_owner_unverified")
            selected.append((entry, object_id))
    if {obj for _, obj in selected} != candidate_ids:
        raise ValueError("core_backup_gc_candidate_missing")
    proof = sha256("\n".join(sorted(candidate_ids)).encode()).hexdigest()
    return selected, proof


def _record(path: Path, source: Path, object_id: str) -> None:
    info = os.lstat(source)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("core_backup_gc_source_changed")
    payload = {
        "schema": _SCHEMA, "source_name": source.name, "object_id": object_id,
        "inode": info.st_ino, "device": info.st_dev,
    }
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write((json.dumps(payload, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    _sync(path.parent)


def _resume(backups: Path, quarantine: Path) -> None:
    for receipt in sorted(quarantine.glob("*.json")):
        key = receipt.name.removesuffix(".json")
        if _SHA.fullmatch(key) is None or receipt.is_symlink():
            raise ValueError("core_backup_gc_audit_unqualified")
        fd = os.open(receipt, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or info.st_mode & 0o077
                    or not 0 < info.st_size <= 8192):
                raise ValueError("core_backup_gc_audit_unqualified")
            raw = os.read(fd, 8193)
            if len(raw) != info.st_size:
                raise ValueError("core_backup_gc_audit_unqualified")
        finally:
            os.close(fd)
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ValueError("core_backup_gc_audit_unqualified") from None
        if (type(record) is not dict or record.get("schema") != _SCHEMA
                or type(record.get("source_name")) is not str
                or _NAME.fullmatch(record["source_name"]) is None
                or record.get("object_id") != _opaque_id(record["source_name"])
                or sha256(record["object_id"].encode()).hexdigest() != key
                or type(record.get("inode")) is not int
                or type(record.get("device")) is not int):
            raise ValueError("core_backup_gc_audit_unqualified")
        source = backups / record["source_name"]
        moved = quarantine / key
        if moved.is_symlink():
            raise ValueError("core_backup_gc_quarantine_changed")
        expected = record["device"], record["inode"]
        if moved.exists():
            info = os.lstat(moved)
            if (not stat.S_ISDIR(info.st_mode)
                    or (info.st_dev, info.st_ino) != expected
                    or source.exists() or source.is_symlink()):
                raise ValueError("core_backup_gc_pending_conflict")
            shutil.rmtree(moved)
            _sync(quarantine)
        elif source.exists():
            info = os.lstat(source)
            if (info.st_dev, info.st_ino) != expected:
                raise ValueError("core_backup_gc_source_changed")
            receipt.unlink()
            _sync(quarantine)


def core_backup_maintenance(
    runtime_root: Path, backups_root: Path, *, now: float | None = None,
    apply: bool = False, acknowledge: str | None = None,
) -> dict:
    """Qualified plan; destructive execution requires an exact acknowledged hash."""
    from . import updater
    runtime, backups = Path(runtime_root), Path(backups_root)
    instant = time.time() if now is None else now
    if not apply:
        selected, digest = _plan(runtime, backups, instant)
        return {"schema": _SCHEMA, "mode": "plan", "safe_to_apply": False,
                "candidate_backups": len(selected), "acknowledgement_sha256": digest}
    if type(acknowledge) is not str or _SHA.fullmatch(acknowledge) is None:
        raise ValueError("core_backup_gc_acknowledgement_required")
    with updater._exclusive_updater_lock(runtime):
        with updater._trusted_package_lease():
            selected, digest = _plan(runtime, backups, instant)
            if digest != acknowledge:
                raise ValueError("core_backup_gc_plan_changed")
            quarantine = backups.parent / ".cifereca-core-backup-gc"
            if quarantine.is_symlink():
                raise ValueError("core_backup_gc_quarantine_unsafe")
            if quarantine.exists():
                if (not quarantine.is_dir() or quarantine.stat().st_uid != os.getuid()
                        or quarantine.stat().st_mode & 0o077):
                    raise ValueError("core_backup_gc_quarantine_unsafe")
                _resume(backups, quarantine)
            if not selected:
                return {"schema": _SCHEMA, "mode": "apply", "removed": 0}
            quarantine.mkdir(mode=0o700, exist_ok=True)
            if quarantine.stat().st_mode & 0o077:
                raise ValueError("core_backup_gc_quarantine_unsafe")
            removed = 0
            for source, object_id in selected:
                key = sha256(object_id.encode()).hexdigest()
                receipt, moved = quarantine / (key + ".json"), quarantine / key
                if receipt.exists() or receipt.is_symlink() or moved.exists():
                    raise ValueError("core_backup_gc_recovery_pending")
                _record(receipt, source, object_id)
                os.replace(source, moved)
                _sync(backups)
                _sync(quarantine)
                shutil.rmtree(moved)
                _sync(quarantine)
                removed += 1
            return {"schema": _SCHEMA, "mode": "apply", "removed": removed}


__all__ = ["core_backup_maintenance"]
