"""P0 #5069: first executable maintenance unit, completed transaction histories.

This is intentionally scoped to committed transaction folders. The planner
decides retention; this executor never deletes releases, plugins or user files.
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

from .updater_retention_inventory import (
    _name_id, inspect_core_transaction_history, _read_transaction_for_inspection,
)

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
_ALLOWED_BLOCKERS = {"partial_store_coverage", "inventory_only_no_gc_implementation"}
_FINAL = {"applied", "rolled_back", "update_failed_restored"}
_SCHEMA = "cifereca.updater.transaction-gc/1"


def _sync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_receipt(path: Path, record: dict) -> None:
    data = (json.dumps(record, sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    _sync_dir(path.parent)


def _qualified(root: Path, *, now: float) -> tuple[dict, list[Path]]:
    report = inspect_core_transaction_history(root, now=now)
    plan = report.get("plan")
    blockers = set(report.get("blockers", ()))
    if (type(plan) is not dict or type(plan.get("objects")) is not list
            or blockers - _ALLOWED_BLOCKERS
            or set(plan.get("blockers", ())) - {"inventory_only_no_gc_implementation"}):
        raise ValueError("transaction_gc_inventory_incomplete")
    decisions = {row["object_id"]: row for row in plan["objects"]
                 if type(row) is dict and type(row.get("object_id")) is str}
    folders: list[Path] = []
    transactions = root / "transactions"
    if transactions.is_symlink():
        raise ValueError("transaction_gc_unsafe_root")
    if transactions.is_dir():
        for entry in sorted(transactions.iterdir()):
            if (entry.is_symlink() or not entry.is_dir()
                    or _ID.fullmatch(entry.name) is None):
                raise ValueError("transaction_gc_unsafe_entry")
            row = decisions.get(_name_id("transaction", entry.name))
            if row is None:
                raise ValueError("transaction_gc_missing_decision")
            if row.get("decision") != "candidate":
                continue
            if row.get("reasons") != []:
                raise ValueError("transaction_gc_invalid_candidate")
            tx = _read_transaction_for_inspection(entry)
            if tx.get("state") not in _FINAL or tx.get("id") != entry.name:
                raise ValueError("transaction_gc_incomplete_receipt")
            # The transaction snapshot is physically inside this folder.
            # Its separately planned generation must ALSO be a candidate.
            snapshot = entry / "plugins"
            if snapshot.is_symlink():
                raise ValueError("transaction_gc_unsafe_snapshot")
            if snapshot.exists():
                snap = decisions.get(_name_id("snapshot", entry.name))
                if (not snapshot.is_dir() or snap is None
                        or snap.get("decision") != "candidate"
                        or snap.get("reasons") != []):
                    continue
            folders.append(entry)
    return report, folders


def transaction_maintenance(root: Path, *, apply: bool = False,
                            acknowledge: str | None = None,
                            now: float | None = None) -> dict:
    """Plan or apply with the same OS lease as Core install and rollback.

    A write-ahead receipt survives rename/crash; pending folders are never
    mistaken for active transactions. No automatic admission of foreign paths.
    """
    from . import updater
    root = Path(root)
    now = time.time() if now is None else now
    if not apply:
        _, folders = _qualified(root, now=now)
        digest = sha256("\n".join(p.name for p in folders).encode()).hexdigest()
        return {"schema": _SCHEMA, "mode": "plan", "safe_to_apply": False,
                "candidate_transactions": len(folders), "acknowledgement_sha256": digest}
    if type(acknowledge) is not str or not re.fullmatch(r"[a-f0-9]{64}", acknowledge):
        raise ValueError("transaction_gc_acknowledgement_required")
    with updater._exclusive_updater_lock(root):
        with updater._trusted_package_lease():
            _, folders = _qualified(root, now=now)
            digest = sha256("\n".join(p.name for p in folders).encode()).hexdigest()
            if digest != acknowledge:
                raise ValueError("transaction_gc_plan_changed")
            if not folders:
                return {"schema": _SCHEMA, "mode": "apply", "removed": 0}
            quarantine = root / "transaction-gc"
            if quarantine.is_symlink():
                raise ValueError("transaction_gc_unsafe_quarantine")
            quarantine.mkdir(mode=0o700, exist_ok=True)
            if quarantine.stat().st_uid != os.getuid() or quarantine.stat().st_mode & 0o077:
                raise ValueError("transaction_gc_unsafe_quarantine")
            removed = 0
            for source in folders:
                # Under the updater lock, a completed journal cannot become
                # active through any cooperating updater operation.
                _qualified(root, now=now)
                destination = quarantine / source.name
                receipt = quarantine / (source.name + ".json")
                if destination.exists() or receipt.exists() or destination.is_symlink():
                    raise ValueError("transaction_gc_pending_recovery_required")
                info = os.lstat(source)
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError("transaction_gc_source_changed")
                _write_receipt(receipt, {
                    "schema": _SCHEMA, "transaction_sha256":
                    sha256(source.name.encode()).hexdigest(),
                    "source_inode": info.st_ino, "source_device": info.st_dev,
                    "state": "pending_delete",
                })
                os.replace(source, destination)
                _sync_dir(source.parent)
                _sync_dir(quarantine)
                shutil.rmtree(destination)
                _sync_dir(quarantine)
                removed += 1
            return {"schema": _SCHEMA, "mode": "apply", "removed": removed}
