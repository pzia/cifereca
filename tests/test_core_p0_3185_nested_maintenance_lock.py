"""#3185: an opt-in Core broker may share GTK's real acquisition lease."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cifereca.locking import InterProcessLock, LockBusyError


def test_same_thread_opt_in_nested_lease_does_not_unlock_host(tmp_path: Path):
    path = tmp_path / "acquisition.lock"
    with InterProcessLock(path) as host:
        with InterProcessLock(path, reentrant=True) as broker:
            assert host._handle is broker._handle
            with pytest.raises(LockBusyError):
                with InterProcessLock(path):
                    pass
        with pytest.raises(LockBusyError):
            with InterProcessLock(path):
                pass
    with InterProcessLock(path):
        pass


def test_other_thread_cannot_join_host_even_with_opt_in(tmp_path: Path):
    path = tmp_path / "acquisition.lock"
    with InterProcessLock(path):
        def acquire_elsewhere():
            try:
                with InterProcessLock(path, reentrant=True):
                    return True
            except LockBusyError:
                return False
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(acquire_elsewhere).result(timeout=5) is False


def test_reverse_release_order_retains_real_os_exclusivity(tmp_path: Path):
    path = tmp_path / "acquisition.lock"
    host = InterProcessLock(path)
    broker = InterProcessLock(path, reentrant=True)
    host.acquire()
    try:
        broker.acquire()
        host.release()
        with pytest.raises(LockBusyError):
            with InterProcessLock(path):
                pass
    finally:
        broker.release()
        host.release()
    with InterProcessLock(path):
        pass


def test_unheld_opt_in_still_acquires_real_os_lock(tmp_path: Path):
    path = tmp_path / "acquisition.lock"
    with InterProcessLock(path, reentrant=True):
        with pytest.raises(LockBusyError):
            with InterProcessLock(path):
                pass


def test_replaced_inode_cannot_borrow_old_lease(tmp_path: Path):
    path = tmp_path / "acquisition.lock"
    replacement = tmp_path / "replacement.lock"
    with InterProcessLock(path):
        replacement.write_text("pid=unknown\n")
        replacement.replace(path)
        with pytest.raises(LockBusyError):
            with InterProcessLock(path, reentrant=True):
                pass


def test_double_acquire_same_object_is_rejected(tmp_path: Path):
    path = tmp_path / "acquisition.lock"
    lock = InterProcessLock(path, reentrant=True)
    lock.acquire()
    try:
        with pytest.raises(RuntimeError, match="already acquired"):
            lock.acquire()
    finally:
        lock.release()
    with InterProcessLock(path):
        pass
