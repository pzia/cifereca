"""#6937: private v1 region registration, not a retention authorization.

Unwired Core-only foundation. No caller may adopt a pre-existing directory
by name, remove an interrupted provision, expose a plugin path, or claim
consumer exclusivity from POSIX permissions. Legacy stages are untouched.
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
from secrets import token_hex
import stat
import sqlite3

from .canonical_dedup_exclusions import ordinary_dedup_path_excluded
from .derived_variant_journal import DerivedJournalConflict, _encode, _unique_pairs
from .derived_variant_retention_review import _directory
from .derived_variant_supervised_recovery import _lock_current
from .library_inventory import _open_root

_REGION = ".cifereca-derived-v1"
_MARKER = ".core-owner-v1"
_PREFIX = "derived.publication.region.v1:"
_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_MAX_RECORD = 8192


def _require(ok, code="derived_region_unverified"):
    if not ok:
        raise DerivedJournalConflict(code)


def _directory_stamp(fd):
    info = os.fstat(fd)
    _require(stat.S_ISDIR(info.st_mode))
    return (info.st_dev, info.st_ino)


def _mount_id(fd):
    # Linux fdinfo identifies the mount of the opened inode. st_dev alone
    # does not exclude distinct bind mounts (hardlink can fail with EXDEV).
    try:
        with open(f"/proc/self/fdinfo/{fd}", "r", encoding="ascii") as stream:
            values = [line.split(":", 1)[1].strip() for line in stream
                      if line.startswith("mnt_id:")]
        _require(len(values) == 1 and values[0].isascii() and values[0].isdigit(),
                 "derived_region_mount_unverified")
        return int(values[0])
    except (OSError, UnicodeError, ValueError):
        raise DerivedJournalConflict("derived_region_mount_unverified") from None


def _private_directory(fd, *, exact=False):
    info = os.fstat(fd)
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
             and (stat.S_IMODE(info.st_mode) == 0o700 if exact else
                  not info.st_mode & 0o022))
    return _directory_stamp(fd)


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _key(identity_id):
    return _PREFIX + hashlib.sha256(identity_id.encode("utf-8")).hexdigest()


def _marker(region_fd):
    fd = os.open(_MARKER, _FLAGS, dir_fd=region_fd)
    try:
        before = os.fstat(fd)
        _require(_mount_id(fd) == _mount_id(region_fd), "derived_region_filesystem_mismatch")
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                 and before.st_uid == os.geteuid()
                 and stat.S_IMODE(before.st_mode) == 0o600 and before.st_size == 32)
        data = os.read(fd, 33)
        after = os.fstat(fd)
        named = os.stat(_MARKER, dir_fd=region_fd, follow_symlinks=False)
        _require(len(data) == 32 and all(ch in b"0123456789abcdef" for ch in data)
                 and _signature(before) == _signature(after) == _signature(named)
                 and named.st_nlink == 1)
        return data.decode("ascii"), list(_signature(before))
    finally:
        os.close(fd)


def _read_record(db, key):
    row = db.execute(
        "SELECT CASE WHEN length(CAST(value AS BLOB)) BETWEEN 1 AND ? "
        "THEN value ELSE NULL END FROM metadata WHERE key=?",
        (_MAX_RECORD, key),
    ).fetchone()
    if row is None:
        return None
    _require(type(row[0]) is str)
    try:
        record = json.loads(row[0], object_pairs_hook=_unique_pairs)
        _require(type(record) is dict and _encode(record) == row[0])
        return row[0]
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise DerivedJournalConflict("derived_region_unverified") from None


def _ensure_region(db, *, storage, root, identity_id, placement_id, shared_lock,
                   scope_current, provision):
    def live():
        _require(_lock_current(shared_lock) and scope_current(db) is True,
                 "derived_region_scope_changed")

    live()
    directory = _directory(db, identity_id)
    profile = root / directory
    region = profile / _REGION
    def parent_row():
        return db.execute(
            "SELECT p.identity_id,p.path,p.state,p.artifact_id,a.sha256,a.size "
            "FROM placements p LEFT JOIN artifacts a ON a.artifact_id=p.artifact_id "
            "WHERE p.placement_id=?", (placement_id,),
        ).fetchone()

    row = parent_row()
    _require(row is not None and row[0] == identity_id and row[2] == "present"
             and type(row[1]) is str)
    digest, expected_size = row[4], row[5]
    _require(type(digest) is str and len(digest) == 64
             and all(ch in "0123456789abcdef" for ch in digest)
             and row[3] == "sha256:" + digest
             and type(expected_size) is int and expected_size > 0,
             "derived_region_parent_unverified")
    parent = Path(row[1])
    _require(str(parent) == row[1] and parent.is_absolute() and ".." not in parent.parts
             and parent.is_relative_to(profile) and parent != profile
             and not any(part.startswith(".") for part in parent.relative_to(profile).parts)
             and not ordinary_dedup_path_excluded(parent, library_root=root,
                         identity_id=identity_id, owner_directory=directory))
    key = _key(identity_id)
    raw = _read_record(db, key)

    with ExitStack() as stack:
        def opened(path):
            fd = _open_root(path)
            stack.callback(os.close, fd)
            return fd

        root_fd, profile_fd, parent_dir = map(opened, (root, profile, parent.parent))
        root_stamp = _directory_stamp(root_fd)
        profile_stamp = _private_directory(profile_fd)
        parent_dir_stamp = _directory_stamp(parent_dir)
        parent_fd = os.open(parent.name, _FLAGS, dir_fd=parent_dir)
        stack.callback(os.close, parent_fd)
        parent_info = os.fstat(parent_fd)
        _require(stat.S_ISREG(parent_info.st_mode) and parent_info.st_size > 0
                 and parent_info.st_dev == parent_dir_stamp[0] == profile_stamp[0],
                 "derived_region_filesystem_mismatch")
        root_mount, profile_mount, parent_mount = map(_mount_id, (root_fd, profile_fd, parent_dir))
        _require(profile_mount == parent_mount == _mount_id(parent_fd),
                 "derived_region_filesystem_mismatch")
        parent_stamp = _signature(parent_info)
        # The name and identity alone do not attest the parent Artifact.
        # Hash the very descriptor whose mount and inode were checked, bounded
        # by the persisted size (not a second path-based open).
        _require(parent_info.st_size == expected_size, "derived_region_parent_unverified")
        checksum = hashlib.sha256()
        remaining = expected_size
        while remaining:
            block = os.read(parent_fd, min(remaining, 1024 * 1024))
            _require(bool(block), "derived_region_parent_unverified")
            checksum.update(block)
            remaining -= len(block)
        _require(checksum.hexdigest() == digest
                 and _signature(os.fstat(parent_fd)) == parent_stamp,
                 "derived_region_parent_unverified")

        def scope_paths_current():
            live()
            _require(_directory(db, identity_id) == directory
                     and parent_row() == row,
                     "derived_region_scope_changed")
            for path, expected, expected_mount in (
                    (root, root_stamp, root_mount), (profile, profile_stamp, profile_mount),
                    (parent.parent, parent_dir_stamp, parent_mount)):
                fd = _open_root(path)
                try:
                    _require(_directory_stamp(fd) == expected and _mount_id(fd) == expected_mount,
                             "derived_region_scope_changed")
                finally:
                    os.close(fd)
            # A bind mount of the same file can preserve stat identity.
            # Reopen the name through the pinned parent directory to
            # compare its current mount as well as its physical signature.
            fresh_parent = os.open(parent.name, _FLAGS, dir_fd=parent_dir)
            try:
                named = os.fstat(fresh_parent)
                _require(stat.S_ISREG(named.st_mode)
                         and _signature(named) == _signature(os.fstat(parent_fd)) == parent_stamp
                         and _mount_id(fresh_parent) == _mount_id(parent_fd) == parent_mount,
                         "derived_region_scope_changed")
            finally:
                os.close(fresh_parent)

        scope_paths_current()
        created = raw is None
        if created:
            _require(provision, "derived_region_not_registered")
            # mkdir is exclusive. A crash/failure after creation intentionally
            # leaves an unregistered name: no implicit adoption or cleanup.
            try:
                os.mkdir(_REGION, mode=0o700, dir_fd=profile_fd)
            except FileExistsError:
                raise DerivedJournalConflict("derived_region_unregistered_name") from None
        region_fd = os.open(_REGION, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                            os.O_CLOEXEC, dir_fd=profile_fd)
        stack.callback(os.close, region_fd)
        region_stamp = _private_directory(region_fd, exact=True)
        _require(region_stamp[0] == profile_stamp[0] and _mount_id(region_fd) == profile_mount,
                 "derived_region_filesystem_mismatch")
        if created:
            scope_paths_current()
            _require(not os.listdir(region_fd), "derived_region_unregistered_name")
            fd = os.open(_MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                         os.O_CLOEXEC, 0o600, dir_fd=region_fd)
            with os.fdopen(fd, "wb") as out:
                out.write(token_hex(16).encode("ascii"))
                out.flush()
                os.fsync(out.fileno())
            os.fsync(region_fd)
            os.fsync(profile_fd)

        nonce, marker_stamp = _marker(region_fd)
        payload = _encode({
            "schema": "derived.region.v1", "identity": identity_id,
            "root": str(root), "directory": directory, "root_stamp": list(root_stamp),
            "profile_stamp": list(profile_stamp), "region_stamp": list(region_stamp),
            "nonce": nonce, "marker_stamp": marker_stamp,
        })
        _require(len(payload.encode("ascii")) <= _MAX_RECORD)
        if created:
            db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (key, payload))
        else:
            _require(payload == raw, "derived_region_binding_changed")

        scope_paths_current()
        recheck = _open_root(region)
        try:
            _require(_private_directory(recheck, exact=True) == region_stamp
                     and _mount_id(recheck) == _mount_id(region_fd) == profile_mount
                     and _private_directory(region_fd, exact=True) == region_stamp
                     and _private_directory(profile_fd) == profile_stamp
                     and _marker(recheck) == (nonce, marker_stamp)
                     and _read_record(db, key) == payload,
                     "derived_region_binding_changed")
        finally:
            os.close(recheck)
        live()
    return {"status": "registered" if created else "verified",
            "region_binding_verified": True, "same_filesystem_verified": True,
            "consumer_exclusivity_verified": False,
            "retention_authorized": False, "destructive_cleanup_allowed": False}


def ensure_private_derived_region(storage, *, library_root, identity_id, placement_id,
                                  shared_lock, scope_current, explicitly_requested=False,
                                  provision=False):
    """Provision only in an explicit isolated Core call; existing binding is read-only.

    Caller holds the exact acquisition lock and a transaction-safe scope
    predicate. No live broker uses this entrypoint yet. Failure after mkdir
    is deliberately non-recoverable automatically, and preserves all bytes.
    The returned aggregate is NOT an FS lease or proof of consumer isolation.
    """
    _require(explicitly_requested is True and type(provision) is bool
             and isinstance(library_root, Path) and library_root.is_absolute()
             and library_root != Path("/") and ".." not in library_root.parts
             and all(type(x) is str and 0 < len(x) <= 1024
                     and not any(ord(ch) < 32 or ord(ch) == 127 for ch in x)
                     for x in (identity_id, placement_id))
             and callable(scope_current) and _lock_current(shared_lock),
             "derived_region_approval_required")
    try:
        with storage._lock:
            db = storage._connect()
            _require(not db.in_transaction, "derived_region_nested_transaction")
            sync = db.execute("PRAGMA synchronous").fetchone()[0]
            try:
                if provision:
                    db.execute("PRAGMA synchronous=FULL")
                with db:
                    db.execute("BEGIN IMMEDIATE" if provision else "BEGIN")
                    result = _ensure_region(
                        db, storage=storage, root=library_root, identity_id=identity_id,
                        placement_id=placement_id, shared_lock=shared_lock,
                        scope_current=scope_current, provision=provision,
                    )
            finally:
                if provision:
                    db.execute(f"PRAGMA synchronous={int(sync)}")
        return result
    except DerivedJournalConflict:
        raise
    except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error):
        raise DerivedJournalConflict("derived_region_unverified") from None
