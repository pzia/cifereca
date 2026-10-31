"""#3185: preserving every source provenance through supervised physical dedup."""
from pathlib import Path

from tests.test_core_p0_3185_canonical_backup_relink import rig
from tests.test_core_p0_3185_dedup_supervisor import inspect
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, store


def provenance(storage):
    with storage._lock, storage._connect() as db:
        origins = db.execute("SELECT * FROM origin_links ORDER BY origin_link_id").fetchall()
        sources = db.execute("SELECT * FROM source_references ORDER BY source_reference_id").fetchall()
    return origins, sources


def test_all_provenances_survive_same_profile_retirement_and_other_profile(rig):
    storage, _root, paths, pubs, _lock = rig
    before = provenance(storage)
    assert len(before[0]) == len(before[1]) == 3
    result = inspect(rig, action="commit", confirmed=True)
    assert result.status == "retired"
    assert provenance(storage) == before
    assert storage.placement_get(pubs[1].placement_id) is None
    assert storage.placement_get(pubs[0].placement_id)["state"] == "present"
    assert storage.placement_get(pubs[2].placement_id)["state"] == "present"
    assert not paths[1].exists()
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES


def test_provenances_unchanged_across_unlink_interruption_and_explicit_recovery(
    rig, monkeypatch,
):
    storage, _root, paths, pubs, _lock = rig
    before = provenance(storage)
    real_unlink = Path.unlink
    with monkeypatch.context() as patch:
        def fail_once(path, *args, **kwargs):
            if path == paths[1]:
                raise PermissionError("synthetic_failure")
            return real_unlink(path, *args, **kwargs)
        patch.setattr(Path, "unlink", fail_once)
        pending = inspect(rig, action="commit", confirmed=True)
    assert pending.status == "cleanup_pending"
    assert provenance(storage) == before
    assert all(path.exists() for path in paths)
    restored = inspect(rig, action="recover", confirmed=True)
    assert restored.status == "retired"
    assert provenance(storage) == before
    assert storage.placement_get(pubs[2].placement_id)["state"] == "present"
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES
