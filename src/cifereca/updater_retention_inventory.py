"""P0 #5069 B2 — read-only, explicitly partial updater store inventory.

Never scan user libraries, never delete, hash arbitrary directory names before
projection. Under-qualified stores remain out of GC eligibility, not a permit.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from hashlib import sha256
import json
import os
import stat
from pathlib import Path
import re
from typing import Any

from . import updater
from .updater_retention import RetentionObject, plan_retention


_FINAL = {"applied", "rolled_back", "update_failed_restored"}
_UNFINISHED = {
    "prepared", "applying", "restoring", "rolling_back",
    "restoration_incomplete", "reconciled_degraded",
}
_PENDING_STORES = (
    "plugin_versions", "updater_backups", "persistent_staging",
)


def _name_id(prefix: str, name: str) -> str:
    # Source paths and package names may contain sensitive user information.
    return prefix + ":" + sha256(name.encode("utf-8")).hexdigest()


def _created_timestamp(value: Any) -> float | None:
    if type(value) is not str or not re.fullmatch(r"\d{8}T\d{6}Z", value):
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc,
        ).timestamp()
    except (OverflowError, ValueError):
        return None



def _read_transaction_for_inspection(folder: Path) -> dict[str, Any]:
    """Anchor each directory/receipt to a NOFOLLOW descriptor before reading.

    The normal transactional reader is for trusted updater writes. An
    inventory must not follow a journal symlink swapped after its initial
    pathname check; all openat calls are tied to one checked directory tree.
    """
    dir_flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                 | getattr(os, "O_NOFOLLOW", 0))
    parent_fd = os.open(folder.parent, dir_flags)
    try:
        folder_fd = os.open(folder.name, dir_flags, dir_fd=parent_fd)
        try:
            file_fd = os.open(
                "transaction.json",
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=folder_fd,
            )
            with os.fdopen(file_fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or not 0 < info.st_size <= 32 * 1024 * 1024):
                    raise ValueError("unsafe_journal")
                raw = stream.read(32 * 1024 * 1024 + 1)
                if len(raw) > 32 * 1024 * 1024:
                    raise ValueError("oversized_journal")
        finally:
            os.close(folder_fd)
    finally:
        os.close(parent_fd)
    value = json.loads(raw.decode("utf-8"))
    if type(value) is not dict or value.get("schema") != updater.TRANSACTION_SCHEMA:
        raise ValueError("unqualified_journal")
    return value


_MAX_RELEASE_RECEIPT_BYTES = 64 * 1024


def _release_receipt_proof(releases: Path, entry: str) -> tuple[bool, float | None]:
    """Validate a Core install receipt using anchored, bounded NOFOLLOW reads.

    A pathname check alone can be raced between is_file() and read_text().
    The receipt is a *necessary* provenance witness, not sufficient ownership
    evidence for a future destructive garbage collector.
    """
    directory_flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                       | getattr(os, "O_NOFOLLOW", 0))
    parent_fd = os.open(releases, directory_flags)
    try:
        release_fd = os.open(entry, directory_flags, dir_fd=parent_fd)
        try:
            directory_info = os.fstat(release_fd)
            receipt_fd = os.open(
                ".cifereca-install.json",
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=release_fd,
            )
            try:
                info = os.fstat(receipt_fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or not 0 < info.st_size <= _MAX_RELEASE_RECEIPT_BYTES):
                    return False, None
                with os.fdopen(receipt_fd, "rb", closefd=False) as stream:
                    raw = stream.read(_MAX_RELEASE_RECEIPT_BYTES + 1)
                if len(raw) != info.st_size:
                    return False, None
            finally:
                os.close(receipt_fd)
        finally:
            os.close(release_fd)
    finally:
        os.close(parent_fd)
    value = json.loads(raw.decode("utf-8"))
    if (type(value) is not dict or type(value.get("version")) is not str
            or not value["version"].strip()):
        return False, None
    stamp = value.get("installed_at")
    if stamp is None:
        # Legacy installed releases predate installed_at. Directory mtime is
        # a conservative fallback, not a provenance proof by itself.
        timestamp = directory_info.st_mtime
    else:
        if type(stamp) is not str or not 15 <= len(stamp) <= 80:
            return False, None
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return False, None
        timestamp = parsed.timestamp()
    if not math.isfinite(timestamp):
        return False, None
    return True, timestamp


def inspect_core_transaction_history(root: Path, *, now: float) -> dict[str, Any]:
    """Return a bounded read-only projection; **never** an apply plan.

    This B2 tranche inventories releases and transaction envelopes only.
    The unqualified plugin version, backup and staging stores remain explicit
    coverage blockers until their owners and references are attested.
    """
    blockers: set[str] = {"partial_store_coverage"}
    objects: list[RetentionObject] = []
    if root.is_symlink():
        return {"schema": "cifereca.updater.retention-inventory/1",
                "safe_to_apply": False,
                "blockers": ["unsafe_runtime_root"],
                "coverage": [], "pending_stores": list(_PENDING_STORES),
                "plan": None}
    releases = root / "releases"
    transactions = root / "transactions"
    if releases.is_symlink():
        blockers.add("unsafe_releases_root")
    if transactions.is_symlink():
        blockers.add("unsafe_transactions_root")

    observed: dict[str, str] = {}
    releases_valid = not releases.is_symlink()
    if releases_valid and releases.is_dir():
        release_items = sorted(releases.iterdir(), key=lambda p: p.name)
        if len(release_items) > 20000:
            blockers.add("release_inventory_too_large")
            releases_valid = False
        else:
            for item in release_items:
                if item.is_symlink():
                    blockers.add("unsafe_release_entry")
                    continue
                if not item.is_dir():
                    blockers.add("unexpected_release_entry")
                    continue
                release_id = _name_id("release", item.name)
                observed[item.name] = release_id
                try:
                    owned, installed_at = _release_receipt_proof(releases, item.name)
                except (OSError, ValueError, UnicodeError, OverflowError):
                    owned, installed_at = False, None
                if not owned:
                    blockers.add("release_ownership_unqualified")
                # No nested recursive traversal, file hashing or plugin call.
                objects.append(RetentionObject(
                    object_id=release_id, store="core_releases",
                    unit="core", generation=release_id,
                    installed_at=installed_at,
                    owned_by_updater=owned,
                ))

    pins: set[str] = set()
    for name in ("current", "previous"):
        link = root / name
        if not link.is_symlink():
            if link.exists():
                blockers.add("invalid_core_pointer")
            continue
        target = updater._symlink_target(link)
        if (target is None or target.parent != releases
                or target.name not in observed
                or target.is_symlink() or not target.is_dir()):
            blockers.add("unresolved_core_pointer")
        else:
            pins.add(observed[target.name])

    if not transactions.is_symlink() and transactions.is_dir():
        entries = sorted(transactions.iterdir(), key=lambda p: p.name)
        if len(entries) > 20000:
            blockers.add("transaction_inventory_too_large")
        else:
            for folder in entries:
                if folder.is_symlink():
                    blockers.add("unsafe_transaction_entry")
                    continue
                if not folder.is_dir():
                    blockers.add("unexpected_transaction_entry")
                    continue
                journal = folder / "transaction.json"
                if journal.is_symlink():
                    blockers.add("unsafe_transaction_journal")
                    continue
                try:
                    tx = _read_transaction_for_inspection(folder)
                except (OSError, ValueError, UnicodeError):
                    blockers.add("transaction_unreadable")
                    continue
                if tx.get("id") != folder.name:
                    blockers.add("transaction_identity_mismatch")
                    continue
                state = tx.get("state")
                if state not in _FINAL | _UNFINISHED:
                    blockers.add("transaction_state_unknown")
                    continue
                core = tx.get("core")
                packages = tx.get("packages")
                if type(core) is not dict or type(packages) is not dict:
                    blockers.add("transaction_proof_invalid")
                    continue
                references: set[str] = set()
                for key in ("before_current", "before_previous", "after"):
                    value = core.get(key)
                    if value is None:
                        continue
                    target = updater._path_from_identity(value)
                    if (target is None or not target.is_absolute()
                            or target.parent != releases or target.name not in observed):
                        # Do not silently orphan references to a missing
                        # generation: fail closed when planner evaluates.
                        references.add(_name_id("missing_core_proof", folder.name + ":" + key))
                        blockers.add("unresolved_transaction_reference")
                    else:
                        references.add(observed[target.name])
                transaction_id = _name_id("transaction", folder.name)
                snapshot = folder / "plugins"
                if snapshot.is_symlink():
                    blockers.add("unsafe_transaction_snapshot")
                elif snapshot.is_dir():
                    snapshot_id = _name_id("snapshot", folder.name)
                    objects.append(RetentionObject(
                        object_id=snapshot_id, store="transaction_snapshots",
                        # Snapshot generations belong to ONE updater history.
                        # A per-transaction unit would always retain its
                        # single generation (min=2) and leak snapshots forever.
                        unit="updater", generation=snapshot_id,
                        installed_at=_created_timestamp(tx.get("created_at")),
                        owned_by_updater=True,
                    ))
                    references.add(snapshot_id)
                elif any(type(v) is dict and type(v.get("before")) is dict
                         and v["before"].get("exists") for v in packages.values()):
                    blockers.add("missing_transaction_snapshot")
                    references.add(_name_id("missing_snapshot_proof", folder.name))
                objects.append(RetentionObject(
                    object_id=transaction_id,
                    store="transactions", unit="updater",
                    generation=transaction_id,
                    installed_at=_created_timestamp(tx.get("created_at")),
                    owned_by_updater=True,
                    pinned=state in _UNFINISHED,
                    references=tuple(sorted(references)),
                ))
    # Apply the Core current/previous pins after all released objects have
    # been listed. These are references, never independent GC candidates.
    objects = [
        RetentionObject(
            object_id=o.object_id, store=o.store, unit=o.unit,
            generation=o.generation, installed_at=o.installed_at,
            owned_by_updater=o.owned_by_updater,
            pinned=o.pinned or o.object_id in pins,
            active=o.active or o.object_id in pins,
            references=o.references,
        )
        for o in objects
    ]
    plan = plan_retention(objects, now=now)
    return {
        "schema": "cifereca.updater.retention-inventory/1",
        "safe_to_apply": False,
        "coverage": ["core_releases", "transactions", "transaction_snapshots"],
        "pending_stores": list(_PENDING_STORES),
        "blockers": sorted(blockers | set(plan["blockers"])),
        "plan": plan,
    }
