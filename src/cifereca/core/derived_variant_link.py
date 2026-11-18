"""#4520: durable, exclusive physical link after a private Core intent.

No authority is created here. The broker must transfer ownership of its
staging lease as soon as reserve() succeeds, before calling the SQL writer.
The helper never removes either staging or destination, including failures.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import os
from pathlib import Path
import stat

from .derived_variant_journal import DerivedIntent, DerivedJournalConflict, _validate
from .library_inventory import _open_root


def _directory_stamp(fd):
    info = os.fstat(fd)
    return (info.st_dev, info.st_ino)


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _regular(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise DerivedJournalConflict("derived_link_physical_changed")
    return _stamp(info)


def _hash(fd, expected_size):
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = expected_size
    while remaining:
        chunk = os.read(fd, min(1024 * 1024, remaining))
        if not chunk:
            raise DerivedJournalConflict("derived_link_physical_changed")
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.hexdigest()


def _require_named(path, directory_fd, file_fd, expected):
    current = _open_root(path.parent)
    try:
        named = os.stat(path.name, dir_fd=current, follow_symlinks=False)
        if (_directory_stamp(current) != _directory_stamp(directory_fd)
                or not stat.S_ISREG(named.st_mode)
                or _stamp(named) != expected or _regular(file_fd) != expected):
            raise DerivedJournalConflict("derived_link_physical_changed")
    finally:
        os.close(current)


def _require_absent(directory_fd, name):
    try:
        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise DerivedJournalConflict("derived_link_filename_conflict")


def link_reserved_variant(
    intent: DerivedIntent, *, source_directory_stamp, staging_directory_stamp,
    staging_root_stamp, reserve, scope_current,
):
    """Reserve one exact target, publish by hardlink, and return its receipt.

    reserve(intent) is an internal durable SQL operation, not a plugin callback;
    it must retain the staging lease on success. A once-reserved collision is
    an unresolved operation: do not fall back or remove competing bytes. All
    directory FDs use no-follow traversal from the root. fsync covers staged
    bytes and directory chain before reservation and the target dir after link.

    No automatic retry, commit or cleanup. A malicious external writer can
    still race the final observation; the canonical SQL writer must recheck.
    """
    _validate(intent)
    parent, stage, candidate = map(Path, (intent.parent_path, intent.staged_path,
                                        intent.candidate_path))
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC

    def current():
        if scope_current() is not True:
            raise DerivedJournalConflict("derived_link_scope_changed")

    try:
        with ExitStack() as stack:
            def opened(path):
                fd = _open_root(path)
                stack.callback(os.close, fd)
                return fd

            def file_opened(path, directory_fd):
                fd = os.open(path.name, flags, dir_fd=directory_fd)
                stack.callback(os.close, fd)
                return fd

            parent_dir = opened(parent.parent)
            stage_root = opened(stage.parent.parent)
            stage_dir = opened(stage.parent)
            if (_directory_stamp(parent_dir) != source_directory_stamp
                    or _directory_stamp(stage_dir) != staging_directory_stamp
                    or _directory_stamp(stage_root) != staging_root_stamp):
                raise DerivedJournalConflict("derived_link_directory_changed")
            source_fd = file_opened(parent, parent_dir)
            stage_fd = file_opened(stage, stage_dir)
            source_stamp = _regular(source_fd)
            output_stamp = _regular(stage_fd)
            if (source_stamp != intent.parent_stamp
                    or output_stamp[:4] != intent.output_stamp
                    or _hash(source_fd, source_stamp[2]) != intent.parent_sha256
                    or _hash(stage_fd, output_stamp[2]) != intent.output_sha256):
                raise DerivedJournalConflict("derived_link_physical_changed")

            def verify_source_and_stage():
                _require_named(parent, parent_dir, source_fd, source_stamp)
                _require_named(stage, stage_dir, stage_fd, output_stamp)
                root_fd = _open_root(stage.parent.parent)
                try:
                    if _directory_stamp(root_fd) != staging_root_stamp:
                        raise DerivedJournalConflict("derived_link_directory_changed")
                finally:
                    os.close(root_fd)

            _require_absent(parent_dir, candidate.name)
            # The reservation only points to a staged file whose inode and
            # containing directory entries have been flushed first.
            for fd in (stage_fd, stage_dir, stage_root, parent_dir):
                os.fsync(fd)
            current()
            verify_source_and_stage()
            _require_absent(parent_dir, candidate.name)
            receipt = reserve(intent)
            # The lease must now survive an exception. Nothing here unlinks.
            current()
            verify_source_and_stage()
            try:
                os.link(stage.name, candidate.name, src_dir_fd=stage_dir,
                        dst_dir_fd=parent_dir, follow_symlinks=False)
            except FileExistsError:
                raise DerivedJournalConflict("derived_link_filename_conflict") from None
            # A hardlink changes ctime, but must preserve dev/ino/size/mtime.
            final_stamp = _regular(stage_fd)
            if (final_stamp[:4] != intent.output_stamp
                    or _hash(stage_fd, final_stamp[2]) != intent.output_sha256):
                raise DerivedJournalConflict("derived_link_physical_changed")
            os.fsync(stage_fd)
            os.fsync(parent_dir)
            current()
            _require_named(parent, parent_dir, source_fd, source_stamp)
            _require_named(stage, stage_dir, stage_fd, final_stamp)
            _require_named(candidate, parent_dir, stage_fd, final_stamp)
            return receipt
    except OSError:
        raise DerivedJournalConflict("derived_link_io_failed") from None
