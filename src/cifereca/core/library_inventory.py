"""Private, bounded, no-write filesystem observations for reconciliation #2565.

Roots are capabilities supplied by the identity-owning caller. This module
never discovers roots, boots LibraryService, imports Storage, or uses network.
Only Inventory.redacted() is suitable for diagnostics/LLM/CI. Observations and
metadata are PRIVATE local evidence, never proof of provider ownership.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import stat
import time
from typing import Callable

from ..epub_metadata import inspect_epub


@dataclass(frozen=True)
class InventoryLimits:
    entries: int = 100_000
    files: int = 4096
    file_bytes: int = 512 * 1024 * 1024
    hash_bytes: int = 64 * 1024 * 1024 * 1024
    seconds: float = 120
    depth: int = 48

    def __post_init__(self):
        ceilings = (100_000, 4096, 512*1024*1024, 64*1024*1024*1024, 600, 48)
        for value, ceiling in zip((self.entries, self.files, self.file_bytes,
                                   self.hash_bytes, self.seconds, self.depth), ceilings):
            if type(value) not in (int, float) or not 0 < value <= ceiling:
                raise ValueError('invalid_inventory_limits')
        if any(type(v) is not int for v in (self.entries, self.files, self.file_bytes,
                                           self.hash_bytes, self.depth)):
            raise ValueError('invalid_inventory_limits')


@dataclass(frozen=True, repr=False)
class FileObservation:
    identity_id: str
    path: str
    size: int
    media_format: str
    sha256: str | None
    validation: str
    metadata: dict = field(default_factory=dict)
    # Descriptor stat fingerprint, NOT a later action authorization.
    fingerprint: tuple = ()


@dataclass(repr=False)
class Inventory:
    files: list[FileObservation] = field(default_factory=list)
    # Full local paths/identities never leave this object via redacted().
    unavailable_directories: list[tuple[str, str]] = field(default_factory=list)
    reasons: set[str] = field(default_factory=set)
    entries: int = 0
    hash_bytes: int = 0
    skipped_symlinks: int = 0
    skipped_special: int = 0

    def redacted(self) -> dict:
        return {
            'schema': 1, 'read_only': True, 'acquisition_authorized': False,
            'status': 'incomplete_scan' if self.reasons else 'complete',
            'reasons': sorted(self.reasons), 'entries': self.entries,
            'files': len(self.files), 'hashed_files': sum(f.sha256 is not None for f in self.files),
            'hash_bytes': self.hash_bytes, 'skipped_symlinks': self.skipped_symlinks,
            'skipped_special': self.skipped_special,
            'unavailable_directories': len(self.unavailable_directories),
            'validation': dict(sorted(Counter(f.validation for f in self.files).items())),
        }


def _fingerprint(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _open_root(root: Path) -> int:
    """O_NOFOLLOW on EVERY ancestor, not just the final directory."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open('/', flags)
    try:
        for piece in root.parts[1:]:
            child = os.open(piece, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def scan_identity_roots(
    roots: dict[str, tuple[Path, ...]], *, limits: InventoryLimits | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[dict], None] | None = None,
) -> Inventory:
    """Recursively inspect ONLY explicit, disjoint, absolute identity roots.

    No symlink/special-file traversal; cancellation/time checked between entries
    and hash chunks. ZIP/XML reads have independent byte/entry ceilings. PDF
    inspection is a signature check only, explicitly not full PDF validation.
    A missing/inaccessible root is inconclusive, never an empty library proof.
    """
    limits = limits or InventoryLimits()
    chosen = []
    if not isinstance(roots, dict) or not 1 <= len(roots) <= 128:
        raise ValueError('invalid_inventory_roots')
    for identity, paths in roots.items():
        if type(identity) is not str or not identity or len(identity) > 1024 or '\0' in identity:
            raise ValueError('invalid_inventory_roots')
        if type(paths) is not tuple or not paths:
            raise ValueError('invalid_inventory_roots')
        for path in paths:
            if len(chosen) >= 128:
                raise ValueError('invalid_inventory_roots')
            path = Path(path)
            if not path.is_absolute() or path == Path('/') or '..' in path.parts:
                raise ValueError('invalid_inventory_roots')
            if any(path.is_relative_to(other) or other.is_relative_to(path) for _, other in chosen):
                raise ValueError('overlapping_inventory_roots')
            chosen.append((identity, path))
    result = Inventory()
    if not all(hasattr(os, name) for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'O_NONBLOCK')):
        result.reasons.add('nofollow_unavailable')
        return result
    deadline = time.monotonic() + limits.seconds

    def stopped():
        if cancelled():
            result.reasons.add('cancelled')
            return True
        if time.monotonic() >= deadline:
            result.reasons.add('time_budget')
            return True
        return False

    def observe(fd, name, identity, path):
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            result.skipped_special += 1
            return
        size = before.st_size
        status = 'uninspected'
        digest = None
        metadata = {}
        with os.fdopen(os.dup(fd), 'rb') as stream:
            if size > limits.file_bytes:
                result.reasons.add('file_byte_budget')
                status = 'file_byte_budget'
            elif size > limits.hash_bytes - result.hash_bytes:
                result.reasons.add('hash_byte_budget')
                status = 'hash_byte_budget'
            else:
                hasher = hashlib.sha256()
                consumed = 0
                while consumed < size:
                    if stopped():
                        return
                    data = stream.read(min(1024*1024, size-consumed))
                    if not data:
                        break
                    result.hash_bytes += len(data)
                    consumed += len(data)
                    hasher.update(data)
                if consumed != size:
                    result.reasons.add('file_changed')
                    return
                digest = hasher.hexdigest()
                stream.seek(0)
                if path.suffix.lower() == '.epub':
                    inspection = inspect_epub(stream, metadata_only=True)
                    status = inspection['epub_status']
                    metadata = inspection['epub_metadata']
                    if status != 'ok':
                        result.reasons.add('epub_parser_limit_or_invalid')
                else:
                    status = 'pdf_signature_only' if stream.read(5) == b'%PDF-' else 'invalid_pdf_signature'
            # Revalidate both opened inode and current pathname after parsing.
            current = os.stat(name, dir_fd=directory_fds[-1], follow_symlinks=False)
            if _fingerprint(before) != _fingerprint(os.fstat(fd)) or _fingerprint(before) != _fingerprint(current):
                result.reasons.add('file_changed')
                return
        result.files.append(FileObservation(identity, str(path), size, path.suffix.lower()[1:],
                                            digest, status, metadata, _fingerprint(before)))

    directory_fds = []

    def walk(fd, identity, path, depth):
        directory_fds.append(fd)
        try:
            with os.scandir(fd) as entries:
                for entry in entries:
                    if stopped():
                        return
                    if result.entries >= limits.entries:
                        result.reasons.add('entry_budget')
                        return
                    result.entries += 1
                    is_directory = False
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISLNK(info.st_mode):
                            result.skipped_symlinks += 1
                        elif stat.S_ISDIR(info.st_mode):
                            is_directory = True
                            if depth >= limits.depth:
                                result.reasons.add('depth_budget')
                                continue
                            child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                            start_index = len(result.files)
                            try:
                                walk(child, identity, path / entry.name, depth+1)
                                opened = os.fstat(child)
                                current = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                                if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                                    del result.files[start_index:]
                                    result.reasons.add('directory_changed')
                            except OSError:
                                del result.files[start_index:]
                                result.reasons.add('directory_changed')
                                raise
                            finally:
                                os.close(child)
                        elif not stat.S_ISREG(info.st_mode):
                            result.skipped_special += 1
                        elif Path(entry.name).suffix.lower() in ('.epub', '.pdf'):
                            if len(result.files) >= limits.files:
                                result.reasons.add('file_budget')
                                return
                            child = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
                            try:
                                observe(child, entry.name, identity, path / entry.name)
                            finally:
                                os.close(child)
                            if progress is not None:
                                progress(result.redacted())
                    except OSError:
                        result.reasons.add('entry_unavailable')
                        if is_directory:
                            result.unavailable_directories.append((identity, str(path / entry.name)))
                    if result.reasons & {'entry_budget', 'file_budget', 'cancelled', 'time_budget'}:
                        return
        except OSError:
            result.reasons.add('directory_unavailable')
            result.unavailable_directories.append((identity, str(path)))
        finally:
            directory_fds.pop()

    for identity, path in chosen:
        if stopped() or result.reasons & {'entry_budget', 'file_budget'}:
            break
        try:
            root_fd = _open_root(path)
            start_index = len(result.files)
            try:
                walk(root_fd, identity, path, 0)
                # A root renamed/replaced during the walk must not retain
                # observations under its former pathname/identity capability.
                current_root = _open_root(path)
                try:
                    before = os.fstat(root_fd)
                    after = os.fstat(current_root)
                    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                        del result.files[start_index:]
                        result.reasons.add('root_changed')
                finally:
                    os.close(current_root)
            except OSError:
                del result.files[start_index:]
                result.reasons.add('root_changed')
                raise
            finally:
                os.close(root_fd)
        except OSError:
            result.reasons.add('directory_unavailable')
            result.unavailable_directories.append((identity, str(path)))
    return result
