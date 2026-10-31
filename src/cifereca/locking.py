from __future__ import annotations

import os
import sys
from pathlib import Path
from threading import get_ident, local
from typing import IO


class LockBusyError(RuntimeError):
    pass


class _HeldLock:
    """One real OS lock, reference-counted within its acquiring thread."""

    def __init__(self, handle: IO[str], *, path: str) -> None:
        self.handle = handle
        self.path = path
        self.pid = os.getpid()
        self.thread_id = get_ident()
        current = os.fstat(handle.fileno())
        self.device = current.st_dev
        self.inode = current.st_ino
        self.references = 1


class InterProcessLock:
    """Small non-blocking process lock backed by an application state file.

    The file itself is intentionally persistent; the operating-system lock is
    what owns exclusivity, so a crash cannot leave a stale logical lock behind.
    Linux/macOS use ``flock``. Windows uses ``msvcrt.locking`` on one byte.
    """

    # Process-local tracking is scoped to the actually acquiring thread.
    # A default nested acquire remains busy, exactly as before.
    _thread = local()

    def __init__(self, path: Path, *, reentrant: bool = False):
        self.path = Path(path)
        self._handle: IO[str] | None = None
        self._lease: _HeldLock | None = None
        self._reentrant = reentrant

    @classmethod
    def _registry(cls) -> dict[str, _HeldLock]:
        value = getattr(cls._thread, "locks", None)
        if value is None:
            value = {}
            cls._thread.locks = value
        return value

    def acquire(self) -> None:
        if self._lease is not None:
            raise RuntimeError("InterProcessLock already acquired")
        # Keep the caller's absolute lexical pathname stable even if a
        # malicious writer replaces its target with a symlink mid-lock.
        # The inode check below must reject that replacement, not silently
        # treat the new target as an unrelated freely acquirable lock.
        key = os.path.normcase(
            os.path.abspath(os.path.expanduser(str(self.path)))
        )
        registry = self._registry()
        if self._reentrant and key in registry:
            held = registry[key]
            try:
                current = os.stat(self.path)
                valid = (
                    held.pid == os.getpid()
                    and held.thread_id == get_ident()
                    and not held.handle.closed
                    and current.st_dev == held.device
                    and current.st_ino == held.inode
                )
            except OSError:
                valid = False
            if not valid:
                raise LockBusyError(str(self.path))
            held.references += 1
            self._lease = held
            self._handle = held.handle
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise LockBusyError(str(self.path)) from exc
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise LockBusyError(str(self.path)) from exc
            handle.seek(0)
            handle.truncate(0)
            handle.write(f"pid={os.getpid()}\n")
            handle.flush()
            try:
                os.fchmod(handle.fileno(), 0o600)
            except OSError:
                pass
            self._lease = _HeldLock(handle, path=key)
            registry[key] = self._lease
            self._handle = handle
        except Exception:
            handle.close()
            raise

    def release(self) -> None:
        held = self._lease
        if held is None:
            return
        if held.pid != os.getpid() or held.thread_id != get_ident():
            # Forked or cross-thread Python state is NOT an OS lease grant.
            raise RuntimeError("InterProcessLock released outside owning thread")
        self._lease = None
        self._handle = None
        held.references -= 1
        if held.references:
            return
        registry = self._registry()
        if registry.get(held.path) is held:
            del registry[held.path]
        handle = held.handle
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            handle.close()

    def __enter__(self) -> "InterProcessLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
