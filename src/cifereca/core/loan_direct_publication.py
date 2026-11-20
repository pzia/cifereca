"""#5225: durable no-clobber publication of a staged direct digital loan.

This remains a safety bridge for the historical path, NOT the canonical
FS/SQLite receipt transaction. The caller owns source admission and indexing.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import secrets
import stat

from .library_inventory import _open_root


class DirectLoanPublicationRefused(ValueError):
    """Closed technical reason; do not include source paths or provider data."""


def _bounded_stem(path: Path) -> str:
    stem = path.stem.encode("utf-8")[:120].decode("utf-8", "ignore")
    return stem.strip(" .-_") or "livre"


def _directory_stamp(fd):
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _current_directory(path, fd):
    other = None
    try:
        other = _open_root(path)
        return _directory_stamp(other) == _directory_stamp(fd)
    except OSError:
        return False
    finally:
        if other is not None:
            os.close(other)


def _create_directory(path):
    """Create only missing components under pinned, no-follow parents."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            try:
                child = os.open(component, flags, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                else:
                    # The newly created directory entry itself must survive
                    # restart before a final-name durability claim.
                    os.fsync(fd)
                child = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        result, fd = fd, None
        return result
    finally:
        if fd is not None:
            os.close(fd)


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _info(fd, name):
    return os.stat(name, dir_fd=fd, follow_symlinks=False)


def _owned(fd, name, expected):
    try:
        actual = _info(fd, name)
        return stat.S_ISREG(actual.st_mode) and _stamp(actual) == _stamp(expected)
    except OSError:
        return False


def _remove_own(fd, name, expected):
    if _owned(fd, name, expected):
        try:
            os.unlink(name, dir_fd=fd)
        except OSError:
            pass


def publish_direct_loan_candidate(
    staged_path: Path, *, work_dir: Path, library_target_dir: Path,
) -> Path:
    """Copy and fsync before an exclusive link on the destination filesystem.

    Retain the input on interruption and never follow or unlink a substituted
    directory/entry. An ambiguous name is not authority to delete. Only after
    the exact final entry is durable may the matching private input retire.
    """
    paths = (staged_path, work_dir, library_target_dir)
    if (not all(isinstance(path, Path) and path.is_absolute()
                and ".." not in path.parts and path != Path("/") for path in paths)
            or not staged_path.name or not staged_path.suffix
            or len(staged_path.name.encode("utf-8")) > 240):
        raise DirectLoanPublicationRefused("loan_direct_candidate_invalid")
    if (staged_path.parent != work_dir or staged_path.is_symlink()
            or any(path.is_symlink() for path in (
                work_dir, *work_dir.parents, library_target_dir, *library_target_dir.parents,
            ))):
        raise DirectLoanPublicationRefused("loan_direct_stage_scope_invalid")

    stage_fd = dest_fd = None
    temporary = None
    made = None
    linked = None
    try:
        try:
            stage_fd = _open_root(work_dir)
            work_stat = _info(stage_fd, staged_path.name)
            if (not stat.S_ISREG(work_stat.st_mode)
                    or work_stat.st_size <= 0 or work_stat.st_nlink != 1):
                raise DirectLoanPublicationRefused("loan_direct_stage_invalid")
        except OSError:
            raise DirectLoanPublicationRefused("loan_direct_stage_unavailable") from None

        dest_fd = _create_directory(library_target_dir)
        if not _current_directory(library_target_dir, dest_fd):
            raise DirectLoanPublicationRefused("loan_direct_target_scope_invalid")
        src_fd = os.open(staged_path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=stage_fd)
        with os.fdopen(src_fd, "rb") as src:
            current = os.fstat(src.fileno())
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                    or _stamp(current) != _stamp(work_stat)):
                raise DirectLoanPublicationRefused("loan_direct_stage_changed")
            temporary = ".loan-publish-" + secrets.token_hex(16)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                         | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dest_fd)
            digest = hashlib.sha256()
            with os.fdopen(fd, "wb") as out:
                # Track our inode even on partial write; cleanup may only
                # withdraw the exact entry we created under the pinned fd.
                made = os.fstat(out.fileno())
                try:
                    while True:
                        part = src.read(1024 * 1024)
                        if not part:
                            break
                        digest.update(part)
                        out.write(part)
                    out.flush()
                    os.fsync(out.fileno())
                finally:
                    made = os.fstat(out.fileno())
            if _stamp(os.fstat(src.fileno())) != _stamp(current):
                raise DirectLoanPublicationRefused("loan_direct_stage_changed")
        if (not _current_directory(work_dir, stage_fd)
                or not _owned(stage_fd, staged_path.name, work_stat)):
            raise DirectLoanPublicationRefused("loan_direct_stage_changed")

        hashed = digest.hexdigest()
        stem, suffix = _bounded_stem(staged_path), staged_path.suffix
        choices = (staged_path.name, f"{stem} - {hashed[:16]}{suffix}",
                   f"{stem} - {hashed}{suffix}")
        for index in range(35):
            name = (choices[index] if index < len(choices)
                    else f"{stem} - {hashed[:16]}-{index - 2}{suffix}")
            if not _current_directory(library_target_dir, dest_fd):
                raise DirectLoanPublicationRefused("loan_direct_target_scope_invalid")
            if not _owned(dest_fd, temporary, made):
                raise DirectLoanPublicationRefused("loan_direct_temp_changed")
            try:
                os.link(temporary, name, src_dir_fd=dest_fd, dst_dir_fd=dest_fd,
                        follow_symlinks=False)
            except FileExistsError:
                continue
            linked = name
            if not _owned(dest_fd, name, made):
                raise DirectLoanPublicationRefused("loan_direct_temp_changed")
            if not _current_directory(library_target_dir, dest_fd):
                raise DirectLoanPublicationRefused("loan_direct_target_scope_invalid")
            # The final directory entry, not just the contents, is durable
            # before reporting a path or retiring the only private input.
            os.fsync(dest_fd)
            if (not _current_directory(library_target_dir, dest_fd)
                    or not _owned(dest_fd, name, made)):
                raise DirectLoanPublicationRefused("loan_direct_target_scope_invalid")

            if (_current_directory(work_dir, stage_fd)
                    and _owned(stage_fd, staged_path.name, work_stat)):
                _remove_own(stage_fd, staged_path.name, work_stat)
            linked = None  # committed physical path; preserve on return
            return library_target_dir / name
        raise DirectLoanPublicationRefused("loan_direct_destination_collisions")
    except DirectLoanPublicationRefused:
        raise
    except OSError:
        raise DirectLoanPublicationRefused("loan_direct_publication_failed") from None
    finally:
        if dest_fd is not None:
            if linked is not None and made is not None:
                # Refused or interrupted publication: withdraw only the inode
                # we wrote. Never delete a concurrent replacement.
                _remove_own(dest_fd, linked, made)
                try:
                    os.fsync(dest_fd)
                except OSError:
                    pass
            if temporary is not None and made is not None:
                _remove_own(dest_fd, temporary, made)
            os.close(dest_fd)
        if stage_fd is not None:
            os.close(stage_fd)


__all__ = ["DirectLoanPublicationRefused", "publish_direct_loan_candidate"]
