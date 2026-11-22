"""#4520: read-only observation of journal-referenced staging stock.

The reservation budget is cumulative logical output size, not physical
allocation. This review measures only named, receipt-matching entries.
No directory walk, hash, recovery, quota refund or deletion.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
import time

from .derived_variant_journal import (
    _read_logical_receipt,
    DerivedJournalConflict, _decode, _measure, _PREFIX, _receipt_digest,
)
from .library_inventory import _open_root
from .derived_variant_retention_review import _directory as _retention_directory

_MAX_SECONDS = 3


def _snapshot(storage, identity_id):
    with storage._lock:
        db = storage._connect()
        # A borrowed connection may already own a caller transaction. Never
        # enter its context manager (which could commit/rollback that work).
        if db.in_transaction:
            raise DerivedJournalConflict("derived_stock_nested_transaction")
        with db:
            db.execute("BEGIN")
            # The stock observer and retirement preflight must agree on
            # which exact, unique SQLite profile-directory name is admissible.
            # Merely observing a file never establishes deletion authority.
            directory = _retention_directory(db, identity_id)
            rows, _ = _measure(db)
            records = []
            for key, _ in rows:
                raw = _read_logical_receipt(db, key)
                intent = _decode(key, raw)
                if intent.identity_id == identity_id:
                    records.append((key, _receipt_digest(raw), intent))
            return directory, tuple(records)


def _named_info(path):
    folder = _open_root(path.parent)
    try:
        try:
            return os.stat(path.name, dir_fd=folder, follow_symlinks=False)
        except FileNotFoundError:
            return None
    finally:
        os.close(folder)


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _observed(info):
    if info is None:
        return None
    return (*_stamp(info), info.st_mode, info.st_ctime_ns, info.st_nlink,
            getattr(info, "st_blocks", None))


def review_derived_staging_stock(storage, *, library_root: Path, identity_id: str):
    """Aggregate one owner, deduplicate hardlinks, never expose coordinates.

    'observed' means receipt stamp and stable named metadata, NOT SHA proof.
    Absence under an unavailable parent is unknown. Allocated blocks may
    include shared extents and live canonical links; not reclaimable bytes.
    Records without a journal receipt are outside the scope of this report.
    """
    def report(status, records=0, counts=None, logical=0, allocated=0):
        return {
            "status": status, "receipts": records,
            "observations": counts if counts is not None else {
                "stage_only": 0, "same_inode_target": 0,
                "stage_absent": 0, "unverified": 0,
            },
            "observed_unique_inode_logical_bytes": logical,
            "observed_unique_inode_allocated_bytes": allocated,
            "complete_physical_inventory": False,
            "sha_verified": False, "reclaimable_bytes_known": False,
            "quota_refund_authorized": False, "destructive_cleanup_allowed": False,
        }

    if (not isinstance(library_root, Path) or not library_root.is_absolute()
            or library_root == Path("/") or ".." in library_root.parts
            or type(identity_id) is not str or not 0 < len(identity_id) <= 1024
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in identity_id)):
        return report("unavailable")
    root_fd = None
    try:
        deadline = time.monotonic() + _MAX_SECONDS
        root_fd = _open_root(library_root)
        root_info = os.fstat(root_fd)
        snapshot = _snapshot(storage, identity_id)
        directory, records = snapshot
        profile = library_root / directory
        counts = dict.fromkeys(("stage_only", "same_inode_target", "stage_absent", "unverified"), 0)
        observed = []
        inodes = {}
        for key, _receipt, intent in records:
            if time.monotonic() >= deadline:
                return report("budget_exceeded", len(records))
            parent, stage, target = map(Path, (intent.parent_path, intent.staged_path,
                                             intent.candidate_path))
            if any(path == profile or not path.is_relative_to(profile)
                   for path in (parent, stage, target)):
                counts["unverified"] += 1
                continue
            try:
                info = _named_info(stage)
                if info is None:
                    counts["stage_absent"] += 1
                    observed.append((stage, None))
                    continue
                if (not stat.S_ISREG(info.st_mode) or _stamp(info) != intent.output_stamp
                        or type(getattr(info, "st_blocks", None)) is not int
                        or info.st_blocks < 0):
                    counts["unverified"] += 1
                    continue
                other = _named_info(target)
                # A committed receipt with an absent/divergent target is not
                # evidence of excess stock. A pending stage may precede link.
                if other is None and key.startswith(_PREFIX):
                    category = "stage_only"
                elif other is not None and _observed(other) == _observed(info):
                    category = "same_inode_target"
                else:
                    counts["unverified"] += 1
                    continue
                inode = (info.st_dev, info.st_ino)
                prior = inodes.get(inode)
                if prior is not None and prior != _observed(info):
                    return report("evidence_changed", len(records))
                inodes[inode] = _observed(info)
                observed.extend(((stage, _observed(info)), (target, _observed(other))))
                counts[category] += 1
            except (OSError, ValueError, RuntimeError):
                counts["unverified"] += 1
        if _snapshot(storage, identity_id) != snapshot:
            return report("evidence_changed", len(records))
        for path, expected in observed:
            if time.monotonic() >= deadline:
                return report("budget_exceeded", len(records))
            if _observed(_named_info(path)) != expected:
                return report("evidence_changed", len(records))
        current = _open_root(library_root)
        try:
            now = os.fstat(current)
            if (now.st_dev, now.st_ino) != (root_info.st_dev, root_info.st_ino):
                return report("evidence_changed", len(records))
        finally:
            os.close(current)
        return report(
            "partial" if counts["unverified"] else "journal_entries_observed",
            len(records), counts, sum(value[2] for value in inodes.values()),
            sum(value[-1] * 512 for value in inodes.values()),
        )
    except (DerivedJournalConflict, sqlite3.Error, OSError, ValueError,
            TypeError, RuntimeError, RecursionError):
        return report("unavailable")
    finally:
        if root_fd is not None:
            os.close(root_fd)
