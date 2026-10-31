"""#3185: concurrent physical mutations cannot authorize a pending tombstone."""
from pathlib import Path

import pytest

from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, store
from tests.test_core_p0_3185_canonical_backup_relink import rig
from tests.test_core_p0_3185_dedup_supervisor import inspect


@pytest.mark.parametrize("race", ["replacement", "symlink", "keeper_corrupt"])
def test_pending_retirement_rejects_physical_races(rig, monkeypatch, race):
    storage, _root, paths, pubs, _lock = rig
    before = Path.unlink
    with monkeypatch.context() as patch:
        def refuse_candidate(path, *args, **kwargs):
            if path == paths[1]:
                raise PermissionError("simulate_crash_after_tombstone")
            return before(path, *args, **kwargs)
        patch.setattr(Path, "unlink", refuse_candidate)
        staged = inspect(rig, action="commit", confirmed=True)
    assert staged.status == "cleanup_pending"
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"
    if race == "replacement":
        paths[1].unlink()
        paths[1].write_bytes(BYTES)
    elif race == "symlink":
        paths[1].unlink()
        paths[1].symlink_to(paths[0])
    else:
        paths[0].write_bytes(b"%PDF-1.7\ncorrupted keeper\n")
    pending = inspect(rig, action="recover", confirmed=True)
    assert pending.status == "cleanup_pending"
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"
    if race == "replacement":
        assert paths[1].read_bytes() == BYTES
    elif race == "symlink":
        assert paths[1].is_symlink()
        assert paths[0].read_bytes() == BYTES
    else:
        assert paths[1].read_bytes() == BYTES
    # No exception/retry may turn uncertain physical identity into permission
    # to unlink unrelated bytes, even if their content digest is unchanged.
