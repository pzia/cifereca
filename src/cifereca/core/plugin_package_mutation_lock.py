"""P0 #5069 A3 — shared, fail-closed lease for managed package-store writes.

This lock is intentionally *not* held across Core's child plugin installer:
the child itself obtains the same lease around its individual package writes.
The supervised reconciliation acquires the updater lease first and this
package lease second, preventing package changes during its proof/commit.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import threading
from typing import Iterator


_THREAD_SERIAL = threading.RLock()
_OWNED = threading.local()
_LOCK_NAME = ".cifereca-managed-package-mutation.lock"


@contextmanager
def package_mutation_lease(package_root: Path) -> Iterator[None]:
    """Reentrant within one thread, mutually exclusive across OS processes.

    Store root is the PackageStore.root, not install_root(). No caller should
    use an unrelated lock path or invent a lock bypass. Linux POSIX flock is
    required; absence or unsafe ownership fails closed.
    """
    parent = Path(package_root).parent
    with _THREAD_SERIAL:
        key = str(parent.absolute())
        owned = getattr(_OWNED, "paths", set())
        if key in owned:
            yield
            return
        if parent.is_symlink():
            raise RuntimeError("package_mutation_directory_unsafe")
        try:
            import fcntl
            parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                parent / _LOCK_NAME,
                os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except (ImportError, OSError):
            raise RuntimeError("package_mutation_lock_unavailable") from None
        try:
            file = os.fstat(descriptor)
            if (not stat.S_ISREG(file.st_mode) or file.st_nlink != 1
                    or file.st_uid != os.getuid()
                    or file.st_mode & 0o077):
                raise RuntimeError("package_mutation_lock_unsafe")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError("package_mutation_busy") from None
            owned.add(key)
            _OWNED.paths = owned
            try:
                yield
            finally:
                owned.remove(key)
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
