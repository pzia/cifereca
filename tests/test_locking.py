from pathlib import Path

import pytest

from cifereca.locking import InterProcessLock, LockBusyError


def test_interprocess_lock_is_exclusive_and_released(tmp_path: Path):
    path = tmp_path / "local-acquisition.lock"
    first = InterProcessLock(path)
    second = InterProcessLock(path)
    first.acquire()
    try:
        with pytest.raises(LockBusyError):
            second.acquire()
        assert path.read_text(encoding="utf-8").startswith("pid=")
    finally:
        first.release()
    second.acquire()
    second.release()
