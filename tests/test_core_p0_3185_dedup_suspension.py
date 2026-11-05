"""P0 #3185 / PU #4496: ordinary deletion is suspended, recovery survives.

These tests deliberately DO NOT enable the historical test-only deletion hook.
They exercise the default product admission on synthetic SQLite and PDF bytes.
"""
from pathlib import Path

import pytest

from cifereca.core.canonical_dedup_supervisor import (
    supervise_canonical_duplicate,
)
from cifereca.core.canonical_dedup_retirement import (
    retire_verified_canonical_duplicate,
)
from cifereca.locking import InterProcessLock
from cifereca.plugin_api import LibraryMutationRequest
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store
from tests.test_core_p0_3185_canonical_backup_relink import rig
from tests.test_core_p0_3185_maintenance_supervisor_broker import broker_for


def _invoke(rig, action="preview", *, confirmed=False):
    storage, root, _paths, pubs, lock_path = rig
    kwargs = dict(
        library_root=root,
        candidate_placement_id=pubs[1].placement_id,
        keeper_placement_id=pubs[0].placement_id,
        action=action,
    )
    if action == "preview":
        return supervise_canonical_duplicate(storage, **kwargs)
    with InterProcessLock(lock_path) as lock:
        return supervise_canonical_duplicate(
            storage, **kwargs, confirmed=confirmed, shared_lock=lock,
        )


def test_preview_remains_readonly_but_confirmed_ordinary_commit_is_suspended(rig):
    storage, _root, paths, pubs, _lock = rig
    original = rows(storage)
    assert _invoke(rig).status == "preview_ready"
    denied = _invoke(rig, "commit", confirmed=True)
    assert (denied.action, denied.status, denied.reason) == (
        "commit", "blocked", "ordinary_cleanup_suspended",
    )
    assert not denied.removed_file
    assert rows(storage) == original == (1, 3, 3, 3)
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert all(path.read_bytes() == BYTES for path in paths)
    assert _invoke(rig).status == "preview_ready"


def test_existing_confirmation_and_lock_preconditions_are_not_relaxed(rig):
    storage, root, paths, pubs, lock_path = rig
    with pytest.raises(ValueError, match="confirmation_and_lock_required"):
        supervise_canonical_duplicate(
            storage, library_root=root,
            candidate_placement_id=pubs[1].placement_id,
            keeper_placement_id=pubs[0].placement_id,
            action="commit", confirmed=True,
            shared_lock=InterProcessLock(lock_path),
        )
    assert all(path.read_bytes() == BYTES for path in paths)


def test_public_canonical_broker_cannot_bypass_default_supervisor_hold(
    rig, monkeypatch,
):
    storage, _root, paths, pubs, _lock = rig
    broker = broker_for(rig, monkeypatch)
    request = LibraryMutationRequest(
        "canonical_dedup", placement_id=pubs[1].placement_id,
        keep_placement_id=pubs[0].placement_id,
        options={"action": "commit", "confirmed": True},
    )
    planned = broker.plan(request)
    assert planned.destructive
    outcome = broker.run(planned)
    assert outcome.ok is False
    assert outcome.changed == 0
    assert outcome.facts["status"] == "blocked"
    assert outcome.facts["reason"] == "ordinary_cleanup_suspended"
    assert rows(storage) == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)


def test_suspended_ordinary_commit_does_not_block_existing_tombstone_recovery(
    rig, monkeypatch,
):
    storage, root, paths, pubs, lock_path = rig
    original_unlink = Path.unlink

    def deny_first_unlink(path, *args, **kwargs):
        if path == paths[1]:
            raise PermissionError("synthetic-first-unlink-refused")
        return original_unlink(path, *args, **kwargs)

    # A tombstone exists from an older, already authorized cleanup.
    # Replaying its safety checks is distinct from making a new keeper choice.
    with InterProcessLock(lock_path) as lock:
        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", deny_first_unlink)
            staged = retire_verified_canonical_duplicate(
                storage, library_root=root,
                candidate_placement_id=pubs[1].placement_id,
                keeper_placement_id=pubs[0].placement_id,
                shared_lock=lock,
            )
    assert staged.retired_placement
    assert staged.cleanup_pending
    assert paths[1].read_bytes() == BYTES
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"

    resumed = _invoke(rig, "recover", confirmed=True)
    assert resumed.status == "retired"
    assert resumed.removed_file
    assert not paths[1].exists()
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES
    assert rows(storage) == (1, 2, 3, 3)
