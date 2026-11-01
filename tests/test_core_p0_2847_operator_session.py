"""#2847: private Core operator dialogue, no automatic repair or token in UI."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.core import canonical_recovery
from cifereca.core.missing_placement_operator import MissingPlacementOperatorSession
from cifereca.locking import InterProcessLock
from tests.test_core_p0_2847_relocation_transaction import catalogue, rig


class _Config:
    def __init__(self, root):
        self.root = str(root)

    def get(self, key, default=None):
        return self.root if key == "library.directory" else default


class _Service:
    def __init__(self, storage, root):
        self.storage = storage
        self.config = _Config(root)
        self.current_identity = SimpleNamespace(identity_id="alice")

    def identity_by_id(self, identity_id):
        return self.storage.identity_get(identity_id)


def _session(rig, *, scope_valid=lambda: True):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    service = _Service(storage, root)
    return service, MissingPlacementOperatorSession(
        service, identity_id="alice", scope_valid=scope_valid,
        inspection_complete=True,
    )


def test_operator_preview_hides_token_and_retire_is_explicit_and_one_shot(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    service, operator = _session(rig)
    preview = operator.preview_retirement(placement_id=pid)
    assert preview == {
        "status": "ready", "reason": "orphan_missing",
        "read_only": True, "requires_explicit_confirmation": True,
    }
    assert "token" not in str(preview)
    assert operator.confirm()["reason"] == "confirmation_required"
    assert storage.placement_get(pid) is not None
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"
    assert operator.preview_retirement(placement_id=pid)["status"] == "ready"
    assert operator.confirm(user_confirmed=True) == {
        "status": "retired", "reason": "orphan_missing", "read_only": False,
    }
    assert storage.placement_get(pid) is None
    assert new.read_bytes() == payload
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"


def test_operator_relocation_only_after_second_confirm_and_catalogue_guards(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    catalogue(rig)
    service, operator = _session(rig)
    first = operator.preview_relocation(placement_id=pid, candidate_path=new)
    assert first == {
        "status": "review_required",
        "reason": "references_require_migration",
        "read_only": True, "requires_explicit_confirmation": True,
        "catalogue_rows": 1, "backup_rows": 0,
    }
    assert str(new) not in str(first)
    assert operator.confirm(user_confirmed=True) == {
        "status": "relocated", "read_only": False, "catalogue_rows": 1,
    }
    assert storage.placement_get(pid)["state"] == "present"
    assert storage.placement_get(pid)["path"] == str(new)
    assert storage.catalog_entry_get("catalogue:original")["metadata"]["path"] == str(new)
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"


def test_identity_switch_revokes_prepared_action(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    service, operator = _session(rig)
    assert operator.preview_retirement(placement_id=pid)["status"] == "ready"
    service.current_identity.identity_id = "bob"
    assert operator.confirm(user_confirmed=True)["reason"] == "scope_changed"
    service.current_identity.identity_id = "alice"
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"
    assert storage.placement_get(pid)["state"] == "missing"


def test_closed_or_stale_session_blocks_even_after_valid_preview(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    valid = [True]
    service, operator = _session(rig, scope_valid=lambda: valid[0])
    assert operator.preview_retirement(placement_id=pid)["status"] == "ready"
    valid[0] = False
    assert operator.confirm(user_confirmed=True)["reason"] == "scope_changed"
    valid[0] = True
    operator.close()
    assert operator.preview_retirement(placement_id=pid)["reason"] == "scope_changed"
    assert storage.placement_get(pid)["state"] == "missing"


def test_partial_inspection_cannot_prepare_retirement_or_relocation(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    service = _Service(storage, root)
    operator = MissingPlacementOperatorSession(
        service, identity_id="alice", scope_valid=lambda: True,
        inspection_complete=False,
    )
    assert operator.preview_retirement(placement_id=pid) == {
        "status": "blocked", "reason": "incomplete_inspection", "read_only": True,
    }
    assert operator.preview_relocation(placement_id=pid, candidate_path=new) == {
        "status": "blocked", "reason": "incomplete_inspection", "read_only": True,
    }
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"
    assert storage.placement_get(pid)["state"] == "missing"
    assert new.read_bytes() == payload


def test_missing_library_root_never_created_by_operator(rig, tmp_path):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    service, operator = _session(rig)
    missing = tmp_path / "unmounted-library"
    service.config.root = str(missing)
    assert operator.preview_retirement(placement_id=pid)["status"] == "blocked"
    assert not missing.exists()
    assert storage.placement_get(pid)["state"] == "missing"


def test_acquisition_lock_contention_fails_closed_without_commit(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    service, operator = _session(rig)
    assert operator.preview_retirement(placement_id=pid)["status"] == "ready"
    with InterProcessLock(canonical_recovery.acquisition_lock_file()):
        outcome = operator.confirm(user_confirmed=True)
    assert outcome["status"] == "blocked"
    assert outcome["reason"] == "acquisition_busy"
    assert storage.placement_get(pid)["state"] == "missing"
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"


def test_selection_change_revokes_old_preview(rig):
    storage, root, old, new, pid, artifact, payload, lockpath = rig
    service, operator = _session(rig)
    assert operator.preview_retirement(placement_id=pid)["status"] == "ready"
    result = operator.preview_relocation(placement_id=pid, candidate_path=new)
    assert result["status"] == "review_required"
    operator.cancel()
    assert operator.confirm(user_confirmed=True)["reason"] == "confirmation_required"
    assert storage.placement_get(pid)["path"] == str(old)
