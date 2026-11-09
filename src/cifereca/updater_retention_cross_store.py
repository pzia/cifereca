"""P0 #5069 B8: read-only transaction -> installed plugin-version references.

The store inventories remain separate. This module observes only bounded,
NOFOLLOW transaction receipts, never plugin payloads, and cannot authorize
any deletion or infer ownership from a hash/name.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
from typing import Any

from .updater_retention_inventory import _name_id, _read_transaction_for_inspection

_MAX_TX = 20_000
_MAX_PACKAGES = 4096
_MAX_VERSIONS = 256
_PACKAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+!-]{0,127}\Z")


def _plugin_version_id(package_id: str, version: str) -> str:
    # Identical opaque identity to the plugin version inventory, but never
    # publish the package ID or version from a private transaction receipt.
    return "plugin_version:" + sha256(
        (package_id + chr(0) + version).encode("utf-8")
    ).hexdigest()


def _version_refs(tx: dict[str, Any]) -> set[str]:
    packages = tx.get("packages")
    if type(packages) is not dict or len(packages) > _MAX_PACKAGES:
        raise ValueError("invalid_package_snapshot")
    refs: set[str] = set()
    for package_id, package in packages.items():
        if (type(package_id) is not str or _PACKAGE.fullmatch(package_id) is None
                or type(package) is not dict):
            raise ValueError("invalid_package_reference")
        for stage in ("before", "after"):
            row = package.get(stage)
            if row is None:
                continue  # prepared transactions may not have after yet
            if type(row) is not dict or type(row.get("exists")) is not bool:
                raise ValueError("invalid_version_witness")
            if row["exists"] is False:
                continue
            versions = row.get("versions")
            if (type(versions) is not list or len(versions) > _MAX_VERSIONS
                    or any(type(value) is not str
                           or _VERSION.fullmatch(value) is None
                           for value in versions)):
                raise ValueError("invalid_version_list")
            if len(set(versions)) != len(versions):
                raise ValueError("duplicate_version")
            refs.update(_plugin_version_id(package_id, version) for version in versions)
    return refs


def inspect_transaction_plugin_references(
    runtime_root: Path, plugin_version_plan: dict[str, Any] | None,
    core_transaction_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Observe a bounded reference edge subset; **always** no-apply.

    Missing or malformed receipts and plugin objects fail closed. A snapshot
    can restore old plugin bytes even if a historical installed version is
    absent; an unresolved reference is therefore a diagnostic blocker, not
    a claim that restoration must fail.
    """
    root = Path(runtime_root)
    folder = root / "transactions"
    blockers: set[str] = {"cross_store_other_edges_unqualified"}
    refs: set[str] = set()
    transactions_seen = 0
    # Candidate transactions remain evidence until independently deleted;
    # this classification does not authorize GC.
    retained_refs: set[str] = set()
    candidate_refs: set[str] = set()
    seen_transaction_ids: set[str] = set()
    plan_decisions: dict[str, str] | None = None
    complete_plan = True
    if core_transaction_plan is not None:
        plan_decisions = {}
        rows = (core_transaction_plan.get("objects")
                if type(core_transaction_plan) is dict else None)
        if type(rows) is not list or len(rows) > _MAX_TX * 4:
            complete_plan = False
        else:
            for row in rows:
                if type(row) is not dict:
                    complete_plan = False
                    continue
                key, decision = row.get("object_id"), row.get("decision")
                if type(key) is not str or not key.startswith("transaction:"):
                    continue
                if (re.fullmatch(r"transaction:[a-f0-9]{64}", key) is None
                        or decision not in ("retain", "candidate")
                        or key in plan_decisions):
                    complete_plan = False
                    continue
                plan_decisions[key] = decision
        if not complete_plan:
            blockers.add("transaction_retention_plan_unqualified")
    if (any(node.is_symlink() for node in (root, *root.parents))
            or folder.is_symlink()
            or (root.exists() and not root.is_dir())
            or (folder.exists() and not folder.is_dir())):
        blockers.add("unsafe_transaction_reference_root")
        entries: list[Path] = []
        complete = False
    elif not folder.exists():
        entries = []
        complete = True
    else:
        try:
            entries = sorted(folder.iterdir(), key=lambda item: item.name)
            complete = len(entries) <= _MAX_TX
            if not complete:
                blockers.add("transaction_reference_inventory_too_large")
                entries = []
        except OSError:
            entries, complete = [], False
            blockers.add("transaction_reference_inventory_unreadable")
    for entry in entries:
        if entry.name.startswith("."):
            # Staging is reported by its own inventory; it has no trustworthy
            # committed transaction receipt yet.
            continue
        if entry.is_symlink() or not entry.is_dir():
            complete = False
            blockers.add("unsafe_transaction_reference_entry")
            continue
        try:
            tx = _read_transaction_for_inspection(entry)
            if tx.get("id") != entry.name:
                raise ValueError("transaction_id_mismatch")
            edges = _version_refs(tx)
            refs.update(edges)
            transactions_seen += 1
            if plan_decisions is not None:
                tx_id = _name_id("transaction", entry.name)
                seen_transaction_ids.add(tx_id)
                decision = plan_decisions.get(tx_id)
                if decision == "retain":
                    retained_refs.update(edges)
                elif decision == "candidate":
                    candidate_refs.update(edges)
                else:
                    complete = False
                    blockers.add("transaction_retention_identity_unqualified")
        except (OSError, UnicodeError, ValueError, TypeError):
            complete = False
            blockers.add("transaction_plugin_reference_unqualified")
    if plan_decisions is not None:
        if not complete_plan or seen_transaction_ids != set(plan_decisions):
            complete = False
            blockers.add("transaction_retention_inventory_mismatch")
    known_ids: set[str] = set()
    if type(plugin_version_plan) is not dict or type(plugin_version_plan.get("objects")) is not list:
        complete = False
        blockers.add("plugin_version_inventory_unavailable")
    else:
        observed = plugin_version_plan["objects"]
        if len(observed) > _MAX_PACKAGES * _MAX_VERSIONS:
            complete = False
            blockers.add("plugin_reference_inventory_too_large")
        else:
            for item in observed:
                value = item.get("object_id") if type(item) is dict else None
                reasons = item.get("reasons") if type(item) is dict else None
                if (type(value) is not str
                        or re.fullmatch(r"plugin_version:[a-f0-9]{64}", value) is None
                        or type(reasons) is not list
                        or any(type(reason) is not str for reason in reasons)
                        or value in known_ids):
                    complete = False
                    blockers.add("plugin_reference_identity_unqualified")
                    continue
                # A hash name alone does not prove an updater-owned version.
                # Only the version inspector's validated receipt can count
                # as resolved; uncertain versions remain unresolved.
                if any(reason in reasons for reason in (
                    "unowned", "unknown_timestamp", "future_timestamp",
                )):
                    blockers.add("plugin_reference_ownership_unqualified")
                    continue
                known_ids.add(value)
    missing = refs - known_ids
    if missing:
        blockers.add("transaction_plugin_version_missing")
    return {
        "schema": "cifereca.updater.cross-store-plugin-references/1",
        "safe_to_apply": False,
        "coverage": ["transaction_plugin_version_edges"] if complete else [],
        "transactions": transactions_seen,
        "plugin_version_edges": len(refs),
        "resolved_edges": len(refs & known_ids),
        "unresolved_edges": len(missing),
        "retained_transaction_edges": (
            len(retained_refs) if plan_decisions is not None and complete else None
        ),
        "candidate_transaction_edges": (
            len(candidate_refs) if plan_decisions is not None and complete else None
        ),
        "retained_unresolved_edges": (
            len(retained_refs - known_ids)
            if plan_decisions is not None and complete else None
        ),
        "blockers": sorted(blockers),
    }
