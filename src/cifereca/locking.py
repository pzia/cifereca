from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import IO


class LockBusyError(RuntimeError):
    pass


class InterProcessLock:
    """Small non-blocking process lock backed by an application state file.

    The file itself is intentionally persistent; the operating-system lock is
    what owns exclusivity, so a crash cannot leave a stale logical lock behind.
    Linux/macOS use ``flock``. Windows uses ``msvcrt.locking`` on one byte.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle: IO[str] | None = None

    def acquire(self) -> None:
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
            self._handle = handle
        except Exception:
            handle.close()
            raise

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
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
