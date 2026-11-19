"""#4520: bounded physical preflight of committed derived receipts.

No SQL canonical/dependency verdict, release handle or reclaimable byte
estimate. Only individual observations, not an atomic physical inventory.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import stat
from time import monotonic

from .derived_variant_journal import DerivedJournalConflict, _COMMITTED, _digest
from .derived_variant_publication import (
    DerivedPublicationConflict, _file_signature, _verified_variant_file,
)
from .derived_variant_stock_review import _snapshot as _stock_snapshot
from .library_inventory import _open_root

_LIMIT = 4
_MAX_BYTES = 1024 ** 3
_SECONDS = 3
_CATEGORIES = ("linked_pair_observed", "stage_absent_target_verified",
               "additional_links_observed", "unverified")


def _report(status, *, total=0, counts=None, checked=0):
    return {
        "status": status,
        "committed_receipts": total,
        "checked": checked,
        "counts": counts if counts is not None else dict.fromkeys(_CATEGORIES, 0),
        "complete_committed_page": status == "observed" and checked == total,
        "complete_physical_inventory": False,
        "canonical_dependencies_verified": False,
        "reclaimable_bytes_known": False,
        "retention_authorized": False,
        "quota_refund_authorized": False,
        "destructive_cleanup_allowed": False,
    }


def _snapshot(storage, identity_id):
    # Storage returns a connection local to the worker thread. Never enter
    # another connection context if that worker already has a transaction:
    # the existing snapshot helper owns its BEGIN/commit boundary.
    if storage._connect().in_transaction:
        raise DerivedJournalConflict("derived_retention_nested_snapshot")
    return _stock_snapshot(storage, identity_id)


def _directory_stamp(fd):
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _name(fd, name):
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return _file_signature(info), info.st_nlink


def _pair(stage, target, signature):
    stage_fd = target_fd = None
    try:
        stage_fd = _open_root(stage.parent)
        target_fd = _open_root(target.parent)
        staging = _name(stage_fd, stage.name)
        canonical = _name(target_fd, target.name)
        if (canonical is None or canonical[0] != signature
                or not stat.S_ISREG(canonical[0][2]) or canonical[1] < 1):
            raise DerivedPublicationConflict("derived_retention_target_changed")
        if staging is None:
            category = ("stage_absent_target_verified" if canonical[1] == 1
                        else "additional_links_observed")
        elif staging != canonical or canonical[1] < 2:
            raise DerivedPublicationConflict("derived_retention_staging_changed")
        else:
            category = ("linked_pair_observed" if canonical[1] == 2
                        else "additional_links_observed")
        return (category, _directory_stamp(stage_fd), _directory_stamp(target_fd),
                staging, canonical)
    finally:
        for fd in (target_fd, stage_fd):
            if fd is not None:
                os.close(fd)


def _observe(intent, profile, snapshot_current):
    parent, stage, target = map(Path, (
        intent.parent_path, intent.staged_path, intent.candidate_path,
    ))
    if any(path == profile or not path.is_relative_to(profile)
           for path in (parent, stage, target)):
        return "unverified"
    try:
        # Hash only the exact parent and canonical target. If stage exists
        # it must be the same pinned inode, never an equal-byte substitute.
        with (
            _verified_variant_file(parent, intent.parent_sha256,
                                   expected_size=intent.parent_stamp[2]) as parent_current,
            _verified_variant_file(target, intent.output_sha256,
                                   expected_size=intent.output_size) as target_current,
        ):
            original, output = parent_current(), target_current()
            if ((original[0], original[1], *original[3:]) != intent.parent_stamp
                    or (output[0], output[1], *output[3:5]) != intent.output_stamp):
                return "unverified"
            first = _pair(stage, target, output)
            if not snapshot_current():
                return "unverified"
            second = _pair(stage, target, output)
            if (second != first or parent_current() != original
                    or target_current() != output):
                return "unverified"
            return first[0]
    except (DerivedPublicationConflict, OSError, ValueError, RuntimeError):
        return "unverified"


def review_committed_derived_physical(storage, *, library_root, identity_id,
                                      explicitly_requested=False):
    """Review at most the first four committed receipts of one exact owner.

    Total hashed bytes are bounded by 1 GiB of attested input/output sizes;
    three seconds is only a cooperative deadline between receipts. The
    root, journal and named inodes are rechecked. A missing staging
    *directory* is unknown, not a proven missing entry.
    """
    if (explicitly_requested is not True
            or not isinstance(library_root, Path) or not library_root.is_absolute()
            or library_root == Path("/") or ".." in library_root.parts
            or type(identity_id) is not str or not 0 < len(identity_id) <= 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in identity_id)):
        return _report("unavailable")
    root_fd = None
    try:
        deadline = monotonic() + _SECONDS
        root_fd = _open_root(library_root)
        root_stamp = _directory_stamp(root_fd)
        snapshot = _snapshot(storage, identity_id)
        directory, records = snapshot
        profile = library_root / directory
        committed = tuple(intent for key, _receipt, intent in records
                          if key.startswith(_COMMITTED))
        counts = dict.fromkeys(_CATEGORIES, 0)
        checked = hashed_budget = 0

        def current():
            if _snapshot(storage, identity_id) != snapshot:
                return False
            fd = _open_root(library_root)
            try:
                return _directory_stamp(fd) == root_stamp
            finally:
                os.close(fd)

        for intent in committed[:_LIMIT]:
            if monotonic() >= deadline:
                break
            cost = intent.parent_stamp[2] + intent.output_size
            if cost > _MAX_BYTES - hashed_budget:
                # The page is incomplete, not an observed negative verdict.
                break
            hashed_budget += cost
            counts[_observe(intent, profile, current)] += 1
            checked += 1
        if not current():
            return _report("evidence_changed")
        return _report("observed", total=len(committed), counts=counts, checked=checked)
    except (DerivedJournalConflict, DerivedPublicationConflict, sqlite3.Error,
            OSError, TypeError, ValueError, RuntimeError, RecursionError):
        return _report("unavailable")
    finally:
        if root_fd is not None:
            os.close(root_fd)


def _review_exact_committed_physical(storage, *, library_root, identity_id,
                                     receipt_key, receipt_sha256,
                                     explicitly_requested=False):
    """Private exact receipt witness for a future combined preflight.

    Use only a key and digest already captured in a private SQL snapshot.
    A positive category cannot authorize retention, quota release, or a
    filesystem operation. No coordinate or assertion of SQL validity is
    returned. A separate live canonical/dependency guard remains required.
    """
    if (explicitly_requested is not True
            or not isinstance(library_root, Path) or not library_root.is_absolute()
            or library_root == Path("/") or ".." in library_root.parts
            or type(identity_id) is not str or not 0 < len(identity_id) <= 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in identity_id)
            or not _digest(receipt_sha256)
            or type(receipt_key) is not str
            or receipt_key != _COMMITTED + receipt_sha256):
        return "unavailable"
    root_fd = None
    try:
        root_fd = _open_root(library_root)
        root_stamp = _directory_stamp(root_fd)
        snapshot = _snapshot(storage, identity_id)
        directory, records = snapshot
        matches = tuple(intent for key, digest, intent in records
                        if key == receipt_key and digest == receipt_sha256)
        if len(matches) != 1:
            return "unavailable"
        intent = matches[0]
        if intent.parent_stamp[2] + intent.output_size > _MAX_BYTES:
            return "budget_exceeded"

        def current():
            if _snapshot(storage, identity_id) != snapshot:
                return False
            fd = _open_root(library_root)
            try:
                return _directory_stamp(fd) == root_stamp
            finally:
                os.close(fd)

        result = _observe(intent, library_root / directory, current)
        return result if current() else "evidence_changed"
    except (DerivedJournalConflict, DerivedPublicationConflict, sqlite3.Error,
            OSError, TypeError, ValueError, RuntimeError, RecursionError):
        return "unavailable"
    finally:
        if root_fd is not None:
            os.close(root_fd)
