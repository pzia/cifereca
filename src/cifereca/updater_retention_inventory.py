"""P0 #5069 B2 — read-only, explicitly partial updater store inventory.

Never scan user libraries, never delete, hash arbitrary directory names before
projection. Under-qualified stores remain out of GC eligibility, not a permit.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
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
                meta = item / ".cifereca-install.json"
                owned = meta.is_file() and not meta.is_symlink()
                if not owned:
                    blockers.add("release_ownership_unqualified")
                # No nested recursive traversal, file hashing or plugin call.
                objects.append(RetentionObject(
                    object_id=release_id, store="core_releases",
                    unit="core", generation=release_id,
                    installed_at=updater._release_installed_at(item),
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
                    if not journal.is_file() or journal.stat().st_size > 32 * 1024 * 1024:
                        blockers.add("transaction_journal_unqualified")
                        continue
                    tx = updater._read_transaction(folder)
                except (updater.UpdateError, OSError):
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
                        unit="transaction", generation=snapshot_id,
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
