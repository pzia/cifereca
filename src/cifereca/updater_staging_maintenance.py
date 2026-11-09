"""P0 #5069: reclaim abandoned nonreusable staging scratch.

This uses *existing* producer receipts and the shared updater/package leases.
Incomplete or unqualified scratch is kept. Scratch is not a reusable backup
generation, so the 24-hour age limit applies without a min-two floor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time

from .updater_retention_staging import (
    _TX_STAGE, _CORE_BACKUP_STAGE, _PLUGIN_STAGE,
)
from .updater_retention_inventory import _read_transaction_for_inspection
from .updater_retention_core_backups import _created_at
from .updater_retention_backups import _timestamp

_SCHEMA = "cifereca.updater.staging-gc/1"
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_LIMIT = 4096


@dataclass(frozen=True)
class _Candidate:
    path: Path
    kind: str
    version: tuple[int, int, int, int]


def _sync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY |
                 getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_receipt(path: Path) -> dict | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            stat_info = os.fstat(fd)
            if (not stat.S_ISREG(stat_info.st_mode)
                    or stat_info.st_nlink != 1
                    or not 0 < stat_info.st_size <= 16384):
                return None
            raw = os.read(fd, 16385)
            if len(raw) != stat_info.st_size:
                return None
        finally:
            os.close(fd)
        receipt = json.loads(raw.decode("utf-8"))
        return receipt if type(receipt) is dict else None
    except (OSError, ValueError, UnicodeError):
        return None


def _proof(path: Path, kind: str) -> float | None:
    """Return a producer-evidenced creation time, never a directory mtime."""
    if kind == "transaction":
        match = _TX_STAGE.fullmatch(path.name)
        if match is None:
            return None
        tx_name = path.name[1:].split(".", 1)[0]
        try:
            tx = _read_transaction_for_inspection(path)
        except (OSError, ValueError, UnicodeError):
            return None
        if (tx.get("id") != tx_name or tx.get("state") != "prepared"
                or tx.get("created_at") != tx_name.split("-", 1)[0]):
            return None
        return _created_at(tx["created_at"])
    if kind == "core_backup":
        match = _CORE_BACKUP_STAGE.fullmatch(path.name)
        if match is None:
            return None
        name = path.name[1:].split(".", 1)[0]
        stamp, digest = name.rsplit("-", 1)
        receipt = _read_receipt(path / "backup.json")
        if (receipt is None or set(receipt) != {"label", "created_at"}
                or type(receipt["label"]) is not str
                or receipt["created_at"] != stamp
                or sha256(receipt["label"].encode()).hexdigest()[:10] != digest):
            return None
        return _created_at(stamp)
    if kind == "plugin":
        match = _PLUGIN_STAGE.fullmatch(path.name)
        if match is None:
            return None
        version = path.name[1:].rsplit("-", 1)[0]
        manifest = _read_receipt(path / "cifereca-plugin.json")
        installed = _read_receipt(path / ".cifereca-install.json")
        pkg = manifest.get("package") if manifest is not None else None
        if (type(pkg) is not dict or pkg.get("id") != path.parent.name
                or pkg.get("version") != version
                or installed is None
                or type(installed.get("sha256")) is not str
                or _HEX.fullmatch(installed["sha256"]) is None):
            return None
        return _timestamp(installed.get("installed_at"))
    return None


def _version(path: Path) -> tuple[int, int, int, int]:
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("staging_gc_not_directory")
    return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns


def _plan(runtime: Path, packages: Path, core_backups: Path, now: float):
    candidates = []
    unqualified = 0

    def collect(parent: Path, kind: str, pattern: re.Pattern) -> None:
        nonlocal unqualified
        if parent.is_symlink() or parent.exists() and not parent.is_dir():
            raise ValueError("staging_gc_store_unsafe")
        if not parent.is_dir():
            return
        entries = sorted(parent.iterdir())
        if len(entries) > _LIMIT:
            raise ValueError("staging_gc_inventory_unbounded")
        for entry in entries:
            if not pattern.fullmatch(entry.name):
                continue
            if entry.is_symlink() or not entry.is_dir():
                unqualified += 1
                continue
            stamp = _proof(entry, kind)
            if (stamp is None or stamp > now
                    or now - stamp < 24 * 3600):
                unqualified += 1
                continue
            final = (
                parent / entry.name[1:].split(".", 1)[0]
                if kind != "plugin" else
                parent / entry.name[1:].rsplit("-", 1)[0]
            )
            if final.exists() or final.is_symlink():
                unqualified += 1
                continue
            candidates.append(_Candidate(entry, kind, _version(entry)))

    transactions = runtime / "transactions"
    collect(transactions, "transaction", _TX_STAGE)
    collect(core_backups, "core_backup", _CORE_BACKUP_STAGE)
    if packages.is_symlink() or packages.exists() and not packages.is_dir():
        raise ValueError("staging_gc_plugins_unsafe")
    if packages.is_dir():
        parents = sorted(packages.iterdir())
        if len(parents) > _LIMIT:
            raise ValueError("staging_gc_plugins_unbounded")
        for package in parents:
            if package.is_symlink() or not package.is_dir():
                raise ValueError("staging_gc_plugin_package_unsafe")
            collect(package, "plugin", _PLUGIN_STAGE)

    # Absolute source paths are included in the acknowledgement: an operator
    # cannot replay the same plan against a different state directory.
    digest = sha256("\n".join(
        f"{x.kind}|{x.path}|{','.join(map(str,x.version))}"
        for x in sorted(candidates, key=lambda x: str(x.path))
    ).encode()).hexdigest()
    return candidates, digest, unqualified


def _quarantine(candidate: _Candidate, runtime: Path, packages: Path,
                core_backups: Path) -> Path:
    if candidate.kind == "transaction":
        return runtime / "staging-gc"
    if candidate.kind == "plugin":
        return packages.parent / ".cifereca-staging-gc"
    return core_backups.parent / ".cifereca-staging-gc"


def _write_audit(quarantine: Path, candidate: _Candidate, token: str) -> None:
    payload = {
        "schema": _SCHEMA, "path": str(candidate.path),
        "kind": candidate.kind, "version": list(candidate.version),
    }
    receipt = quarantine / (token + ".json")
    fd = os.open(receipt, os.O_CREAT | os.O_WRONLY | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write((json.dumps(payload, sort_keys=True) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    _sync(quarantine)


def _resume(quarantine: Path, runtime: Path, packages: Path,
            core_backups: Path) -> None:
    """Complete only a previously audited inode, never a new path occupant."""
    for receipt in sorted(quarantine.glob("*.json")):
        token = receipt.name[:-5]
        if _HEX.fullmatch(token) is None or receipt.is_symlink():
            raise ValueError("staging_gc_audit_unqualified")
        record = _read_receipt(receipt)
        if (record is None or record.get("schema") != _SCHEMA
                or type(record.get("path")) is not str
                or type(record.get("kind")) is not str
                or type(record.get("version")) is not list
                or len(record["version"]) != 4
                or any(type(number) is not int for number in record["version"])):
            raise ValueError("staging_gc_audit_unqualified")
        source = Path(record["path"])
        kind = record["kind"]
        if (not source.is_absolute()
                or sha256((kind + "|" + str(source)).encode()).hexdigest() != token
                or kind not in ("transaction", "core_backup", "plugin")):
            raise ValueError("staging_gc_audit_unqualified")
        expected = _quarantine(
            _Candidate(source, kind, tuple(record["version"])),
            runtime, packages, core_backups,
        )
        valid_scope = (
            (kind == "transaction" and source.parent == runtime / "transactions"
             and _TX_STAGE.fullmatch(source.name) is not None)
            or (kind == "core_backup" and source.parent == core_backups
                and _CORE_BACKUP_STAGE.fullmatch(source.name) is not None)
            or (kind == "plugin" and source.parent.parent == packages
                and _PLUGIN_STAGE.fullmatch(source.name) is not None)
        )
        if not valid_scope or expected != quarantine:
            raise ValueError("staging_gc_audit_unqualified")
        moved = quarantine / token
        if moved.is_symlink() or source.is_symlink():
            raise ValueError("staging_gc_audit_source_changed")
        if moved.exists():
            info = os.lstat(moved)
            if (not stat.S_ISDIR(info.st_mode)
                    or (info.st_dev, info.st_ino) != tuple(record["version"][:2])
                    or source.exists()):
                raise ValueError("staging_gc_pending_conflict")
            shutil.rmtree(moved)
            _sync(quarantine)
        elif source.exists():
            current = _version(source)
            if current != tuple(record["version"]):
                raise ValueError("staging_gc_audit_source_changed")
            receipt.unlink()
            _sync(quarantine)


def staging_maintenance(
    runtime_root: Path, package_root: Path, core_backups_root: Path, *,
    now: float | None = None, apply: bool = False,
    acknowledge: str | None = None,
) -> dict:
    """Explicitly clean witnessed abandoned scratch after 24h under leases."""
    from . import updater
    runtime, packages, core_backups = map(
        Path, (runtime_root, package_root, core_backups_root),
    )
    instant = time.time() if now is None else now
    if not apply:
        entries, proof, unqualified = _plan(runtime, packages, core_backups, instant)
        return {
            "schema": _SCHEMA, "mode": "plan", "safe_to_apply": False,
            "candidate_stages": len(entries), "retained_unqualified": unqualified,
            "acknowledgement_sha256": proof,
        }
    if type(acknowledge) is not str or _HEX.fullmatch(acknowledge) is None:
        raise ValueError("staging_gc_acknowledgement_required")
    with updater._exclusive_updater_lock(runtime):
        with updater._trusted_package_lease():
            entries, proof, ignored = _plan(runtime, packages, core_backups, instant)
            if proof != acknowledge:
                raise ValueError("staging_gc_plan_changed")
            for quarantine in (
                runtime / "staging-gc",
                packages.parent / ".cifereca-staging-gc",
                core_backups.parent / ".cifereca-staging-gc",
            ):
                if quarantine.is_symlink():
                    raise ValueError("staging_gc_quarantine_unsafe")
                if quarantine.is_dir():
                    if quarantine.stat().st_uid != os.getuid() or quarantine.stat().st_mode & 0o077:
                        raise ValueError("staging_gc_quarantine_unsafe")
                    _resume(quarantine, runtime, packages, core_backups)
            removed = 0
            for entry in entries:
                # Only the already witnessed inode is moved. Crash after
                # rename leaves it in quarantine for manual recovery, never
                # triggers a second unlink from the original namespace.
                source = entry.path
                if _version(source) != entry.version:
                    raise ValueError("staging_gc_source_changed")
                quarantine = _quarantine(entry, runtime, packages, core_backups)
                if quarantine.is_symlink():
                    raise ValueError("staging_gc_quarantine_unsafe")
                quarantine.mkdir(mode=0o700, parents=True, exist_ok=True)
                if quarantine.stat().st_mode & 0o077:
                    raise ValueError("staging_gc_quarantine_unsafe")
                token = sha256((entry.kind + "|" + str(source)).encode()).hexdigest()
                destination = quarantine / token
                if (destination.exists() or destination.is_symlink()
                        or (quarantine / (token + ".json")).exists()):
                    raise ValueError("staging_gc_pending_recovery")
                _write_audit(quarantine, entry, token)
                os.replace(source, destination)
                _sync(source.parent)
                _sync(quarantine)
                shutil.rmtree(destination)
                _sync(quarantine)
                removed += 1
            return {"schema": _SCHEMA, "mode": "apply", "removed": removed,
                    "retained_unqualified": ignored}


__all__ = ["staging_maintenance"]
