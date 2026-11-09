"""P0 #5069: apply 24h + min2 to owned historical plugin-version trees.

A plugin is only collectible when the complete retained Core-transaction
reference view admits it. This never deletes active/previous versions, a whole
package, user files, or a plugin installation scratch directory.
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

from .updater_retention_inventory import inspect_core_transaction_history
from .updater_retention_plugins import (
    _identity, _overlay_attested_transaction_pins, inspect_plugin_version_history,
)
from .updater_retention_cross_store import inspect_transaction_plugin_references


_SCHEMA = "cifereca.updater.plugin-gc/1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_KNOWN_PLUGIN_BLOCKERS = {
    "cross_store_references_unqualified", "inventory_only_no_gc_implementation",
}
_KNOWN_CORE_BLOCKERS = {
    "partial_store_coverage", "inventory_only_no_gc_implementation",
}
_KNOWN_REFERENCE_BLOCKERS = {"cross_store_other_edges_unqualified"}
_MAX_CANDIDATES = 4096 * 256


def _sync(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY |
                 getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _plan(runtime: Path, packages: Path, *, now: float) -> tuple[list[tuple[Path, str]], str]:
    core = inspect_core_transaction_history(runtime, now=now)
    plugin = inspect_plugin_version_history(packages, now=now)
    if (set(core["blockers"]) - _KNOWN_CORE_BLOCKERS
            or set(plugin["blockers"]) - _KNOWN_PLUGIN_BLOCKERS):
        raise ValueError("plugin_gc_inventory_unqualified")
    cross = inspect_transaction_plugin_references(
        runtime, plugin.get("plan"), core_transaction_plan=core.get("plan"),
        _include_internal_resolved_refs=True,
    )
    if (set(cross["blockers"]) - _KNOWN_REFERENCE_BLOCKERS
            or cross.get("retention_edges_qualified") is not True
            or cross.get("unresolved_edges") != 0):
        raise ValueError("plugin_gc_cross_store_unqualified")
    retained = cross.pop("_resolved_retained_plugin_ids", None)
    planned = _overlay_attested_transaction_pins(plugin.get("plan"), retained)
    if (type(planned) is not dict
            or planned.get("safe_to_apply") is not False
            or type(planned.get("objects")) is not list
            or type(planned.get("candidates")) is not int):
        raise ValueError("plugin_gc_plan_unqualified")
    candidate_ids = {
        row["object_id"] for row in planned["objects"]
        if type(row) is dict and row.get("decision") == "candidate"
    }
    if len(candidate_ids) != planned["candidates"] or len(candidate_ids) > _MAX_CANDIDATES:
        raise ValueError("plugin_gc_plan_inconsistent")
    for row in planned["objects"]:
        if (type(row) is not dict or type(row.get("object_id")) is not str
                or row.get("decision") not in ("candidate", "retain")
                or type(row.get("reasons")) is not list):
            raise ValueError("plugin_gc_plan_inconsistent")
        if row["object_id"] in candidate_ids and row["reasons"]:
            raise ValueError("plugin_gc_plan_inconsistent")
    if packages.is_symlink() or (packages.exists() and not packages.is_dir()):
        raise ValueError("plugin_gc_package_root_unsafe")

    entries: list[tuple[Path, str]] = []
    seen: set[str] = set()
    if packages.is_dir():
        for package in sorted(packages.iterdir()):
            if package.is_symlink() or not package.is_dir():
                raise ValueError("plugin_gc_package_unqualified")
            # A package's current.json and every version have been reviewed
            # by the shared inventory. Never infer ownership from a name.
            for version in sorted(package.iterdir()):
                if version.name == "current.json" or version.name.startswith("."):
                    continue
                if not version.is_dir() or version.is_symlink():
                    raise ValueError("plugin_gc_version_unqualified")
                identity = _identity(
                    "plugin_version", package.name + "\0" + version.name,
                )
                seen.add(identity)
                if identity in candidate_ids:
                    entries.append((version, identity))
    if not candidate_ids.issubset(seen):
        raise ValueError("plugin_gc_candidate_disappeared")
    return entries, sha256(
        "\n".join(sorted(identity for _, identity in entries)).encode("utf-8")
    ).hexdigest()


def _save_receipt(path: Path, *, source: Path, proof: str) -> None:
    info = os.lstat(source)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("plugin_gc_source_changed")
    content = (json.dumps({
        "schema": _SCHEMA,
        "source_parent": str(source.parent),
        "source_name": source.name,
        "source_inode": info.st_ino,
        "source_device": info.st_dev,
        "object_id": proof,
    }, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_EXCL | os.O_CREAT |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    _sync(path.parent)


def _finish_interrupted(packages: Path, quarantine: Path) -> None:
    """Resume only an exact, privately attested same-filesystem rename."""
    if not quarantine.is_dir() or quarantine.is_symlink():
        raise ValueError("plugin_gc_quarantine_unsafe")
    for receipt in sorted(quarantine.glob("*.json")):
        key = receipt.name.removesuffix(".json")
        if not _HEX.fullmatch(key) or receipt.is_symlink():
            raise ValueError("plugin_gc_audit_invalid")
        fd = os.open(receipt, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or info.st_mode & 0o077
                    or not 0 < info.st_size <= 8192):
                raise ValueError("plugin_gc_audit_invalid")
            raw = os.read(fd, 8193)
            if len(raw) != info.st_size:
                raise ValueError("plugin_gc_audit_invalid")
        finally:
            os.close(fd)
        try:
            record = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ValueError("plugin_gc_audit_invalid") from None
        if type(record) is not dict:
            raise ValueError("plugin_gc_audit_invalid")
        name, parent, proof = (
            record.get("source_name"), record.get("source_parent"),
            record.get("object_id"),
        ) if type(record) is dict else (None, None, None)
        if (record.get("schema") != _SCHEMA or type(name) is not str
                or type(parent) is not str or type(proof) is not str
                or not _HEX.fullmatch(key)
                or sha256(proof.encode()).hexdigest() != key
                or type(record.get("source_inode")) is not int
                or type(record.get("source_device")) is not int):
            raise ValueError("plugin_gc_audit_invalid")
        source = Path(parent) / name
        if (source.parent.parent != packages
                or source.parent.is_symlink() or source.is_symlink()
                or _identity("plugin_version", source.parent.name + "\0" + name) != proof):
            raise ValueError("plugin_gc_audit_outside_store")
        destination = quarantine / key
        original = source
        expected = (record["source_device"], record["source_inode"])
        if destination.is_symlink():
            raise ValueError("plugin_gc_pending_unsafe")
        if destination.exists():
            info = os.lstat(destination)
            if (not stat.S_ISDIR(info.st_mode)
                    or (info.st_dev, info.st_ino) != expected
                    or original.exists() or original.is_symlink()):
                raise ValueError("plugin_gc_pending_conflict")
            shutil.rmtree(destination)
            _sync(quarantine)
        elif original.exists():
            info = os.lstat(original)
            if (info.st_dev, info.st_ino) != expected:
                raise ValueError("plugin_gc_original_changed")
            receipt.unlink()
            _sync(quarantine)


def plugin_history_maintenance(
    runtime_root: Path, packages_root: Path, *,
    now: float | None = None, apply: bool = False,
    acknowledge: str | None = None,
) -> dict[str, Any]:
    """Plan and explicitly apply only qualified historical plugin versions."""
    from . import updater
    runtime, packages = Path(runtime_root), Path(packages_root)
    instant = time.time() if now is None else now
    if not apply:
        entries, proof = _plan(runtime, packages, now=instant)
        return {
            "schema": _SCHEMA, "mode": "plan", "safe_to_apply": False,
            "candidate_versions": len(entries), "acknowledgement_sha256": proof,
        }
    if type(acknowledge) is not str or _HEX.fullmatch(acknowledge) is None:
        raise ValueError("plugin_gc_acknowledgement_required")
    with updater._exclusive_updater_lock(runtime):
        with updater._trusted_package_lease():
            entries, proof = _plan(runtime, packages, now=instant)
            if proof != acknowledge:
                raise ValueError("plugin_gc_plan_changed")
            quarantine = packages.parent / ".cifereca-plugin-gc"
            if quarantine.is_symlink():
                raise ValueError("plugin_gc_quarantine_unsafe")
            if quarantine.exists():
                if (not quarantine.is_dir() or quarantine.stat().st_uid != os.getuid()
                        or quarantine.stat().st_mode & 0o077):
                    raise ValueError("plugin_gc_quarantine_unsafe")
                _finish_interrupted(packages, quarantine)
            if not entries:
                return {"schema": _SCHEMA, "mode": "apply", "removed": 0}
            quarantine.mkdir(mode=0o700, exist_ok=True)
            if quarantine.stat().st_mode & 0o077:
                raise ValueError("plugin_gc_quarantine_unsafe")
            removed = 0
            for source, identity in entries:
                token = sha256(identity.encode()).hexdigest()
                receipt = quarantine / (token + ".json")
                destination = quarantine / token
                if receipt.exists() or receipt.is_symlink() or destination.exists():
                    raise ValueError("plugin_gc_pending_recovery_required")
                _save_receipt(receipt, source=source, proof=identity)
                os.replace(source, destination)
                _sync(source.parent)
                _sync(quarantine)
                shutil.rmtree(destination)
                _sync(quarantine)
                removed += 1
            return {"schema": _SCHEMA, "mode": "apply", "removed": removed}


__all__ = ["plugin_history_maintenance"]
