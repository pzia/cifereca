"""#4708: exact pre/post archive evidence under the caller's transaction.

No writer, no commit, no confirmation or lock acquisition. The future
supervisor must own the acquisition lock and BEGIN IMMEDIATE/FULL, check
the configuration epoch and compare pre/post witnesses before committing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time

from .library_inventory import _open_root
from .duplicate_maintenance_guard import candidate_has_active_dependencies_in_db
from .missing_placement_archive_envelope import _decode_archive_row
from .missing_placement_archive_sql import _schema, _inventory, _live_row
from .missing_placement_dependency_projection import (
    _direct_dependency, _extra_metadata_clear, _reference_matches, _strings,
    _unique_pairs,
)


def _absence_signature(root, profile, parent, leaf):
    """Pin and recheck all three directory names without following symlinks."""
    fds = []
    try:
        for name in (root, profile, parent):
            fds.append(_open_root(name))
        def stamp(fd):
            info = os.fstat(fd)
            return info.st_dev, info.st_ino
        def absent(fd):
            try:
                os.stat(leaf, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return True
            return False
        if not absent(fds[-1]):
            return None
        before = tuple(stamp(fd) for fd in fds)
        for name in (root, profile, parent):
            fds.append(_open_root(name))
        if tuple(stamp(fd) for fd in fds[3:]) != before or not absent(fds[-1]):
            return None
        return before
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _observe_archive_admission(db, *, library_root, identity_id,
                               expected_payload, expected_digest,
                               max_records, max_bytes, phase):
    """Private witness or None; caller must compare it, not serialize it.

    'live' and 'archived' attest exact mutually exclusive SQL states. The
    same owner, root/profile/parent inodes, physical absence and dependency
    guards are required on both sides. A positive observation is not proof
    against external writers, unregistered consumers or all aliases.
    """
    try:
        if (not db.in_transaction or type(phase) is not str
                or phase not in ("live", "archived")
                or type(identity_id) is not str or not identity_id
                or not isinstance(library_root, Path) or library_root.anchor != "/"
                or library_root == Path("/") or ".." in library_root.parts):
            return None
        _schema(db)
        row = _decode_archive_row(expected_payload, expected_digest)
        pid, artifact, path, owner = row[:4]
        if owner != identity_id:
            return None
        directory = db.execute(
            "SELECT directory_name FROM main.identities WHERE identity_id=?", (owner,)
        ).fetchone()
        if (directory is None or type(directory[0]) is not str
                or directory[0] in ("", ".", "..") or "/" in directory[0]
                or chr(92) in directory[0]
                or db.execute("SELECT 1 FROM main.identities WHERE directory_name=? "
                              "AND identity_id<>? LIMIT 1", (directory[0], owner)).fetchone()
                is not None
                or db.execute("SELECT 1 FROM main.artifacts WHERE artifact_id=?",
                              (artifact,)).fetchone() is None):
            return None
        profile = library_root / directory[0]
        stored = Path(path)
        if not stored.is_relative_to(profile) or stored == profile:
            return None
        initial = _absence_signature(library_root, profile, stored.parent, stored.name)
        if initial is None:
            return None
        records, _ = _inventory(db, max_records=max_records, max_bytes=max_bytes)
        live = _live_row(db, pid)
        by_path = db.execute(
            "SELECT placement_id FROM main.placements WHERE path=? LIMIT 2", (path,)
        ).fetchall()
        if phase == "live":
            if live != row or by_path != [(pid,)] or pid in records:
                return None
        elif live is not None or by_path or records.get(pid) != (
                row, expected_payload, expected_digest):
            return None
        for other_id, (other, _payload, other_digest) in records.items():
            if other_id == pid:
                continue
            if (other[2] == path or other_digest == expected_digest
                    or _reference_matches(other[8], (path, pid))
                    or any(_reference_matches(v, (path, pid)) for v in _strings(
                        json.loads(other[8], object_pairs_hook=_unique_pairs)))):
                return None
        if (_direct_dependency(db, dict(placement_id=pid, artifact_id=artifact,
                                        path=path, identity_id=owner))
                or candidate_has_active_dependencies_in_db(db, placement_id=pid, path=path)
                or candidate_has_active_dependencies_in_db(db, placement_id=pid, path=pid)
                or not _extra_metadata_clear(db, (path, pid), deadline=time.monotonic()+2,
                                             cancelled=lambda: False)):
            return None
        final = _absence_signature(library_root, profile, stored.parent, stored.name)
        if initial != final:
            return None
        return (expected_digest, owner, str(library_root), directory[0], initial)
    except (OSError, sqlite3.Error, ValueError, TypeError, RuntimeError, RecursionError):
        return None
