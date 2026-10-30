"""#3185: duplicate cleanup and canonical Sync share one non-blocking lock.

Synthetic local placements only; no supplier I/O and no DRM access.
"""
from pathlib import Path

import pytest

from cifereca import artifact_runtime, paths
from cifereca.locking import InterProcessLock, LockBusyError
from cifereca.plugin_api import Conflict
from tests.test_core_p0_3185_duplicate_dependencies import _rig


def _lock_path(tmp_path, monkeypatch):
    lock_path = tmp_path / "locks" / "acquisition.lock"
    monkeypatch.setattr(paths, "acquisition_lock_file", lambda: lock_path)
    return lock_path


def test_sync_lock_busy_refuses_cleanup_without_waiting_or_mutation(
    tmp_path, monkeypatch,
):
    lock_path = _lock_path(tmp_path, monkeypatch)
    service, broker, request, candidate, kept, _ = _rig(tmp_path)
    plan = broker.plan(request)

    with InterProcessLock(lock_path):
        with pytest.raises(Conflict, match="Bibliothèque occupée"):
            broker.run(plan)
        assert service.storage.placement_get(request.placement_id) is not None
        assert candidate.read_bytes() == kept.read_bytes() == b"book-data"

    # No queued destructive work runs on release: only a fresh user invocation.
    assert service.storage.placement_get(request.placement_id) is not None
    outcome = broker.run(broker.plan(request))
    assert outcome.ok and outcome.changed == 1
    assert not candidate.exists() and kept.is_file()


def test_cleanup_holds_acquisition_lock_until_physical_unlink(
    tmp_path, monkeypatch,
):
    lock_path = _lock_path(tmp_path, monkeypatch)
    service, broker, request, candidate, kept, _ = _rig(tmp_path)
    plan = broker.plan(request)
    original_unlink = Path.unlink
    observed = []

    def checked_unlink(path, *args, **kwargs):
        if path == candidate:
            with pytest.raises(LockBusyError):
                with InterProcessLock(lock_path):
                    pass
            observed.append("locked-during-unlink")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", checked_unlink)
    outcome = broker.run(plan)
    assert outcome.ok and observed == ["locked-during-unlink"]
    assert not candidate.exists() and kept.is_file()
    # The lock is released after a successful cleanup.
    with InterProcessLock(lock_path):
        pass


def test_cleanup_exception_releases_lock_and_keeps_copy(
    tmp_path, monkeypatch,
):
    lock_path = _lock_path(tmp_path, monkeypatch)
    service, broker, request, candidate, kept, _ = _rig(tmp_path)
    plan = broker.plan(request)

    def fail_commit(**_kwargs):
        raise RuntimeError("synthetic SQL fault")

    monkeypatch.setattr(service.storage, "placement_retire_duplicate_atomic", fail_commit)
    with pytest.raises(RuntimeError, match="synthetic SQL fault"):
        broker.run(plan)
    with InterProcessLock(lock_path):
        assert candidate.is_file() and kept.is_file()
        assert service.storage.placement_get(request.placement_id) is not None
