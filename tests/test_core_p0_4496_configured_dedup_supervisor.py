"""#4496: configured private canonical supervisor on isolated real bytes."""
from pathlib import Path

import pytest

from cifereca.config import Config
from cifereca.core import canonical_dedup_supervisor as core
from cifereca.core import canonical_dedup_retirement as retirement
from cifereca.core.destructive_cleanup_policy import DESTRUCTIVE_CLEANUP_KEY as KEY
from cifereca.locking import InterProcessLock
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store
from tests.test_core_p0_3185_canonical_backup_relink import rig


def _config(tmp_path, enabled):
    config = Config(tmp_path / "cleanup-config.json")
    config.set(KEY, enabled)
    config.save()
    return Config(config.path)


def _run(rig, config, action="commit", confirmed=True):
    storage, root, _paths, pubs, lock_path = rig
    with InterProcessLock(lock_path) as lock:
        return core.supervise_canonical_duplicate(
            storage, library_root=root, candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=pubs[0].placement_id, action=action,
            shared_lock=lock, confirmed=confirmed, config=config,
        )


def test_real_config_true_executes_with_confirmation(rig, tmp_path):
    storage, _root, paths, pubs, _lock = rig
    config = _config(tmp_path, True)
    with pytest.raises(ValueError, match="confirmation_and_lock_required"):
        _run(rig, config, confirmed=False)
    assert all(path.read_bytes() == BYTES for path in paths)
    assert _run(rig, config, "preview").status == "preview_ready"
    outcome = _run(rig, config)
    assert outcome.status == "retired" and outcome.removed_file
    assert not paths[1].exists()
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES
    assert rows(storage) == (1, 2, 3, 3)


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_disabled_config_blocks_confirmed_commit(rig, tmp_path, value):
    storage, _root, paths, pubs, _lock = rig
    config = _config(tmp_path, value)
    assert _run(rig, config).reason == "ordinary_cleanup_suspended"
    assert all(path.read_bytes() == BYTES for path in paths)
    assert rows(storage) == (1, 3, 3, 3)


def test_revocation_after_prepare_blocks_retirement(rig, tmp_path, monkeypatch):
    storage, _root, paths, pubs, _lock = rig
    config = _config(tmp_path, True)
    original = core.prepare_verified_canonical_duplicate

    def prepare(*args, **kwargs):
        result = original(*args, **kwargs)
        config.set(KEY, False)
        return result

    monkeypatch.setattr(core, "prepare_verified_canonical_duplicate", prepare)
    assert _run(rig, config).reason == "ordinary_cleanup_suspended"
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert all(path.read_bytes() == BYTES for path in paths)


def test_revocation_after_durable_tombstone_leaves_explicit_recovery(rig, tmp_path, monkeypatch):
    storage, _root, paths, pubs, _lock = rig
    config = _config(tmp_path, True)
    original = retirement.recover_canonical_duplicate_retirement

    def before_unlink(*args, **kwargs):
        config.set(KEY, False)
        return original(*args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(retirement, "recover_canonical_duplicate_retirement", before_unlink)
        outcome = _run(rig, config)
    assert outcome.status == "cleanup_pending" and not outcome.removed_file
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"
    assert all(path.read_bytes() == BYTES for path in paths)
    resumed = _run(rig, config, "recover")
    assert resumed.status == "retired" and resumed.removed_file
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES
    assert not paths[1].exists()


def test_revocation_between_reference_classes_stops_next_relink(rig, tmp_path, monkeypatch):
    from cifereca.core import canonical_dedup_prepare as prepare
    storage, _root, paths, pubs, _lock = rig
    config = _config(tmp_path, True)
    calls = []

    def catalogue(*args, **kwargs):
        calls.append("catalogue")
        config.set(KEY, False)
        return 0

    def forbidden(*args, **kwargs):
        pytest.fail("later reference class invoked after revocation")

    monkeypatch.setattr(prepare, "relink_canonical_duplicate_catalogue", catalogue)
    monkeypatch.setattr(prepare, "relink_canonical_duplicate_backup_paths", forbidden)
    monkeypatch.setattr(prepare, "relink_completed_delivery_checkpoints", forbidden)
    assert _run(rig, config).reason == "ordinary_cleanup_suspended"
    assert calls == ["catalogue"]
    assert all(path.read_bytes() == BYTES for path in paths)
    assert rows(storage) == (1, 3, 3, 3)
