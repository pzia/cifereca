"""#3185: the public broker lists pending canonical cleanups without mutation."""
from pathlib import Path
from types import SimpleNamespace

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.core.canonical_dedup_retirement import (
    retire_verified_canonical_duplicate,
)
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store
from tests.test_core_p0_3719_canonical_duplicate_retirement import rig


def test_readonly_pending_cleanup_broker_scope_and_physical_integrity(rig, monkeypatch):
    storage, root, paths, pubs, _snapshot, lock_path = rig
    origin = Path.unlink

    def deny(path, *args, **kwargs):
        if path == paths[1]:
            raise PermissionError("synthetic-unlink-failed")
        return origin(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", deny)
        with InterProcessLock(lock_path) as lock:
            result = retire_verified_canonical_duplicate(
                storage, library_root=root,
                candidate_placement_id=pubs[1].placement_id,
                keeper_placement_id=pubs[0].placement_id,
                shared_lock=lock,
            )
    assert result.cleanup_pending and result.retired_placement
    before = rows(storage)
    broker = LibraryMaintenanceBrokerImpl(SimpleNamespace(storage=storage))
    scoped = broker.pending_canonical_cleanups(identity_id="A")
    assert scoped.status == "reviewed"
    assert scoped.recovery_candidates == 1
    assert scoped.candidates[0].candidate_placement_id == pubs[1].placement_id
    assert scoped.candidates[0].keeper_placement_id == pubs[0].placement_id
    assert broker.pending_canonical_cleanups(identity_id="B").candidates == ()
    assert rows(storage) == before == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)
