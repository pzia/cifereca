"""P0 #5069: explicit 24h + min2 GC of owned DevUpdater plugin backups.

No Core library, manual backup, live profile, SQLite or credentials root is
eligible. Backup ownership is supplied only by the producer's durable receipt.
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
from typing import Any

from .updater_retention_backups import _id, _NAME, inspect_dev_updater_backups
from .updater_retention_inventory import (
    _read_transaction_for_inspection, _UNFINISHED,
)

_SCHEMA = "cifereca.updater.dev-backup-gc/1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED = {
    "cross_store_backup_dependencies_unqualified",
    "inventory_only_no_gc_implementation",
}


def _sync(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY |
                 getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _no_incomplete_transaction(runtime: Path) -> None:
    root = runtime / "transactions"
    if root.is_symlink():
        raise ValueError("dev_backup_gc_transaction_root_unsafe")
    if not root.exists():
        return
    if not root.is_dir():
        raise ValueError("dev_backup_gc_transaction_root_unsafe")
    entries = list(root.iterdir())
    if len(entries) > 20000:
        raise ValueError("dev_backup_gc_transaction_overflow")
    for folder in entries:
        if folder.name.startswith("."):
            raise ValueError("dev_backup_gc_pending_transaction")
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("dev_backup_gc_transaction_unsafe")
        try:
            tx = _read_transaction_for_inspection(folder)
        except (OSError, UnicodeError, ValueError):
            raise ValueError("dev_backup_gc_transaction_unqualified") from None
        if tx.get("state") in _UNFINISHED:
            raise ValueError("dev_backup_gc_transaction_pending")
        if tx.get("state") not in {"applied", "rolled_back", "update_failed_restored"}:
            raise ValueError("dev_backup_gc_transaction_unknown")


def _plan(runtime: Path, backups: Path, *, now: float) -> tuple[list[tuple[Path, str]], str]:
    _no_incomplete_transaction(runtime)
    report = inspect_dev_updater_backups(backups, now=now)
    plan = report.get("plan")
    if (set(report["blockers"]) - _ALLOWED
            or type(plan) is not dict or type(plan.get("objects")) is not list
            or type(plan.get("candidates")) is not int):
        raise ValueError("dev_backup_gc_inventory_unqualified")
    candidate_ids = {
        row["object_id"] for row in plan["objects"]
        if type(row) is dict and row.get("decision") == "candidate"
    }
    if len(candidate_ids) != plan["candidates"]:
        raise ValueError("dev_backup_gc_plan_inconsistent")
    if backups.is_symlink() or (backups.exists() and not backups.is_dir()):
        raise ValueError("dev_backup_gc_root_unsafe")
    paths = []
    for entry in sorted(backups.iterdir()) if backups.is_dir() else ():
        if entry.is_symlink() or not entry.is_dir():
            raise ValueError("dev_backup_gc_entry_unsafe")
        if _id(entry.name) in candidate_ids:
            paths.append((entry, _id(entry.name)))
    if {identity for _, identity in paths} != candidate_ids:
        raise ValueError("dev_backup_gc_candidate_missing")
    proof = sha256("\n".join(sorted(candidate_ids)).encode()).hexdigest()
    return paths, proof


def _receipt(path: Path, source: Path, identity: str) -> None:
    info = os.lstat(source)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("dev_backup_gc_source_changed")
    record = {
        "schema": _SCHEMA,
        "name": source.name,
        "identity": identity,
        "inode": info.st_ino,
        "device": info.st_dev,
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write((json.dumps(record, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    _sync(path.parent)


def _resume(backups: Path, quarantine: Path) -> None:
    for receipt in sorted(quarantine.glob("*.json")):
        key = receipt.name.removesuffix(".json")
        if _HEX.fullmatch(key) is None or receipt.is_symlink():
            raise ValueError("dev_backup_gc_audit_invalid")
        fd = os.open(receipt, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or info.st_mode & 0o077
                    or not 0 < info.st_size <= 8192):
                raise ValueError("dev_backup_gc_audit_invalid")
            raw = os.read(fd, 8193)
            if len(raw) != info.st_size:
                raise ValueError("dev_backup_gc_audit_invalid")
        finally:
            os.close(fd)
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ValueError("dev_backup_gc_audit_invalid") from None
        if (type(record) is not dict or record.get("schema") != _SCHEMA
                or type(record.get("name")) is not str
                or _NAME.fullmatch(record["name"]) is None
                or record.get("identity") != _id(record["name"])
                or sha256(record["identity"].encode()).hexdigest() != key
                or type(record.get("inode")) is not int
                or type(record.get("device")) is not int):
            raise ValueError("dev_backup_gc_audit_invalid")
        source = backups / record["name"]
        moved = quarantine / key
        if moved.is_symlink():
            raise ValueError("dev_backup_gc_quarantine_changed")
        if moved.exists():
            info = os.lstat(moved)
            if (not stat.S_ISDIR(info.st_mode)
                    or (info.st_dev, info.st_ino) != (
                        record["device"], record["inode"])
                    or source.exists() or source.is_symlink()):
                raise ValueError("dev_backup_gc_pending_conflict")
            shutil.rmtree(moved)
            _sync(quarantine)
        elif source.exists():
            info = os.lstat(source)
            if (info.st_dev, info.st_ino) != (record["device"], record["inode"]):
                raise ValueError("dev_backup_gc_source_changed")
            receipt.unlink()
            _sync(quarantine)


def dev_backup_maintenance(
    runtime_root: Path, backups_root: Path, *, now: float | None = None,
    apply: bool = False, acknowledge: str | None = None,
) -> dict[str, Any]:
    from . import updater
    runtime, backups = Path(runtime_root), Path(backups_root)
    instant = time.time() if now is None else now
    if not apply:
        paths, digest = _plan(runtime, backups, now=instant)
        return {
            "schema": _SCHEMA, "mode": "plan", "safe_to_apply": False,
            "candidate_backups": len(paths), "acknowledgement_sha256": digest,
        }
    if type(acknowledge) is not str or _HEX.fullmatch(acknowledge) is None:
        raise ValueError("dev_backup_gc_acknowledgement_required")
    with updater._exclusive_updater_lock(runtime):
        with updater._trusted_package_lease():
            paths, digest = _plan(runtime, backups, now=instant)
            if digest != acknowledge:
                raise ValueError("dev_backup_gc_plan_changed")
            quarantine = backups.parent / ".cifereca-dev-backup-gc"
            if quarantine.is_symlink():
                raise ValueError("dev_backup_gc_quarantine_unsafe")
            if quarantine.exists():
                if (not quarantine.is_dir() or quarantine.stat().st_uid != os.getuid()
                        or quarantine.stat().st_mode & 0o077):
                    raise ValueError("dev_backup_gc_quarantine_unsafe")
                _resume(backups, quarantine)
            if not paths:
                return {"schema": _SCHEMA, "mode": "apply", "removed": 0}
            quarantine.mkdir(mode=0o700, exist_ok=True)
            if quarantine.stat().st_mode & 0o077:
                raise ValueError("dev_backup_gc_quarantine_unsafe")
            removed = 0
            for source, identity in paths:
                key = sha256(identity.encode()).hexdigest()
                receipt, moved = quarantine / (key + ".json"), quarantine / key
                if receipt.exists() or receipt.is_symlink() or moved.exists():
                    raise ValueError("dev_backup_gc_recovery_pending")
                _receipt(receipt, source, identity)
                os.replace(source, moved)
                _sync(backups)
                _sync(quarantine)
                shutil.rmtree(moved)
                _sync(quarantine)
                removed += 1
            return {"schema": _SCHEMA, "mode": "apply", "removed": removed}


__all__ = ["dev_backup_maintenance"]
