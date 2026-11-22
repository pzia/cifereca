"""#4520: bounded, read-only observation of symlinks in the library root.

Never follows a symlink, opens a linked target or claims absence of external
dependencies. Even an unmatched symlink is an unresolved alias. No writer.
"""
from __future__ import annotations

import os
from pathlib import Path
import posixpath
import sqlite3
import stat
from time import monotonic

from .derived_variant_journal import DerivedJournalConflict, _COMMITTED
from .derived_variant_retention_physical import _snapshot
from .library_inventory import _open_root

_MAX_ENTRIES = 4096
_MAX_DEPTH = 24
_MAX_SECONDS = 3
_MAX_TARGET_BYTES = 4096
_RECEIPT_LIMIT = 4


class _Incomplete(ValueError):
    pass


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _lexical_target(parent, value):
    if not value or len(os.fsencode(value)) > _MAX_TARGET_BYTES or "\x00" in value:
        raise _Incomplete()
    target = posixpath.normpath(posixpath.join(str(parent), value))
    return "/" + target.lstrip("/")


def _walk(profile, stages, *, deadline):
    from .derived_variant_private_region import _mount_id

    # Keep only bounded private witnesses for comparison, not for diagnostics.
    # Directory names are never reopened through a symlink.
    entries = {}
    directories = set()
    direct = unresolved = 0

    def budget():
        if monotonic() >= deadline:
            raise _Incomplete()

    def scan(fd, folder, depth):
        nonlocal direct, unresolved
        budget()
        if depth > _MAX_DEPTH:
            raise _Incomplete()
        before = _stamp(os.fstat(fd))
        # A distinct mount is not covered by the root's observation, even
        # when a bind mount preserves st_dev or points to an unseen inode.
        # An incomplete walk is never absence evidence.
        if (before[0] != root[0] or before[:2] in directories
                or _mount_id(fd) != root_mount):
            raise _Incomplete()
        directories.add(before[:2])
        with os.scandir(fd) as iterator:
            for entry in iterator:
                budget()
                if len(entries) >= _MAX_ENTRIES:
                    raise _Incomplete()
                name = entry.name
                path = folder / name
                info = entry.stat(follow_symlinks=False)
                expected = _stamp(info)
                link = None
                if stat.S_ISLNK(info.st_mode):
                    link = os.readlink(name, dir_fd=fd)
                    # Include a potential alias to a directory containing
                    # a stage; lexical matches are conservative observations.
                    target = _lexical_target(folder, link)
                    if any(stage == target or stage.startswith(target.rstrip("/") + "/")
                           for stage in stages):
                        direct += 1
                    else:
                        unresolved += 1
                entries[str(path)] = (expected, link)
                if stat.S_ISDIR(info.st_mode):
                    if expected[0] != root[0] or expected[:2] in directories:
                        raise _Incomplete()
                    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
                    child = os.open(name, flags, dir_fd=fd)
                    try:
                        if _stamp(os.fstat(child)) != expected:
                            raise _Incomplete()
                        scan(child, path, depth + 1)
                    finally:
                        os.close(child)
                if _stamp(os.stat(name, dir_fd=fd, follow_symlinks=False)) != expected:
                    raise _Incomplete()
        if _stamp(os.fstat(fd)) != before or _mount_id(fd) != root_mount:
            raise _Incomplete()

    fd = _open_root(profile)
    try:
        root = _stamp(os.fstat(fd))
        root_mount = _mount_id(fd)
        scan(fd, profile, 0)
        return root, entries, direct, unresolved
    finally:
        os.close(fd)


def _receipt_alias_verdict(witness, stage):
    """A lexical match to this receipt blocks; every other symlink is unknown.

    The witness is private and never a reusable authority. In particular,
    a direct match to another receipt is not proof of absence for this one.
    """
    unresolved = False
    for name, (_info, link) in witness[1].items():
        if link is None:
            continue
        target = _lexical_target(Path(name).parent, link)
        if stage == target or stage.startswith(target.rstrip("/") + "/"):
            return "known_symlink_dependency"
        unresolved = True
    return "symlink_dependency_unverified" if unresolved else "no_local_symlink_observed"


def review_stage_symlinks(storage, *, library_root, identity_id,
                          explicitly_requested=False):
    """Observe up to four committed staging coordinates against local symlinks.

    Two bounded directory walks must agree, with journal/root revalidation.
    Counts describe symlink entries in the supplied library root, not unique
    receipts; no full FS or external-consumer proof, including symlink chains
    outside that root.
    """
    def report(status, *, checked=0, entries=0, direct=0, unresolved=0):
        return {
            "status": status, "committed_receipts_checked": checked,
            "entries_observed": entries, "lexical_stage_aliases": direct,
            "unresolved_symlinks": unresolved,
            "external_dependencies_verified": False, "complete_physical_inventory": False,
            "retention_authorized": False, "quota_refund_authorized": False,
            "destructive_cleanup_allowed": False,
        }

    if (explicitly_requested is not True or not isinstance(library_root, Path)
            or not library_root.is_absolute() or library_root == Path("/")
            or ".." in library_root.parts or type(identity_id) is not str
            or not 0 < len(identity_id) <= 1024
            or any(ord(c) < 32 or ord(c) == 127 for c in identity_id)):
        return report("unavailable")
    root_fd = None
    try:
        deadline = monotonic() + _MAX_SECONDS
        root_fd = _open_root(library_root)
        root_stamp = _stamp(os.fstat(root_fd))
        snapshot = _snapshot(storage, identity_id)
        directory, records = snapshot
        profile = library_root / directory
        selected = tuple(intent for key, _digest, intent in records
                         if key.startswith(_COMMITTED))[:_RECEIPT_LIMIT]
        if not selected:
            return report("no_committed_receipts")
        if any(not Path(intent.staged_path).is_relative_to(profile)
               or Path(intent.staged_path) == profile for intent in selected):
            return report("unavailable")
        stages = tuple(intent.staged_path for intent in selected)
        # The selected receipts belong to one profile, but a symlink in a
        # sibling profile or unregistered library directory can consume them.
        # Keep the owner's nofollow admission before the broader observation.
        owner_fd = _open_root(profile)
        try:
            owner_stamp = _stamp(os.fstat(owner_fd))
            first = _walk(library_root, stages, deadline=deadline)
            second = _walk(library_root, stages, deadline=deadline)
            if second != first or _snapshot(storage, identity_id) != snapshot:
                return report("evidence_changed")
            fresh_owner = _open_root(profile)
            try:
                if (_stamp(os.fstat(fresh_owner)) != owner_stamp
                        or _stamp(os.fstat(owner_fd)) != owner_stamp):
                    return report("evidence_changed")
            finally:
                os.close(fresh_owner)
        finally:
            os.close(owner_fd)
        current = _open_root(library_root)
        try:
            if _stamp(os.fstat(current)) != root_stamp:
                return report("evidence_changed")
        finally:
            os.close(current)
        if monotonic() >= deadline:
            return report("incomplete")
        return report("observed", checked=len(selected), entries=len(first[1]),
                      direct=first[2], unresolved=first[3])
    except _Incomplete:
        return report("incomplete")
    except (DerivedJournalConflict, sqlite3.Error, OSError, ValueError,
            TypeError, RuntimeError, RecursionError):
        return report("unavailable")
    finally:
        if root_fd is not None:
            os.close(root_fd)
