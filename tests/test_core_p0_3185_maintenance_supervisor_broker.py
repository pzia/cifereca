"""#3185: public maintenance broker is a safe, explicit canonical Core entry point.

All files/accounts, identities and rights are synthetic. The legacy delete
operation may not bypass the new canonical confirmation/lock/recovery flow.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import Conflict, LibraryMutationRequest
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store
from tests.test_core_p0_3185_canonical_backup_relink import rig, backup


def broker_for(rig, monkeypatch, *, root_override=None):
    storage, root, _paths, _pubs, lock_path = rig
    monkeypatch.setattr("cifereca.paths.acquisition_lock_file", lambda: lock_path)
    configured_root = str(root if root_override is None else root_override)
    config = SimpleNamespace(
        get=lambda key, default=None: (
            configured_root if key == "library.directory" else default
        ),
    )
    return LibraryMaintenanceBrokerImpl(SimpleNamespace(storage=storage, config=config))


def request(rig, *, action="preview", confirmed=None, keeper=None, candidate=None):
    _storage, _root, _paths, pubs, _lock = rig
    opts = {"action": action}
    if confirmed is not None:
        opts["confirmed"] = confirmed
    return LibraryMutationRequest(
        "canonical_dedup", placement_id=candidate or pubs[1].placement_id,
        keep_placement_id=keeper or pubs[0].placement_id,
        options=opts,
    )


def test_canonical_broker_default_preview_is_readonly(rig, monkeypatch):
    storage, _root, paths, pubs, _lock = rig
    broker = broker_for(rig, monkeypatch)
    req = LibraryMutationRequest(
        "canonical_dedup", placement_id=pubs[1].placement_id,
        keep_placement_id=pubs[0].placement_id,
    )
    plan = broker.plan(req)
    assert plan.operation == "canonical_dedup" and not plan.destructive
    assert plan.facts == {
        "action": "preview", "reason": "verified_no_active_references",
    }
    before = rows(storage)
    result = broker.run(plan)
    assert result.ok and result.changed == 0
    assert result.facts["status"] == "preview_ready"
    assert result.facts["removed_file"] is False
    assert all(path.read_bytes() == BYTES for path in paths)
    assert rows(storage) == before == (1, 3, 3, 3)


@pytest.mark.parametrize("confirmed", [None, False, 0, "yes"])
def test_no_explicit_boolean_confirmation_never_deletes(rig, monkeypatch, confirmed):
    storage, _root, paths, pubs, _lock = rig
    broker = broker_for(rig, monkeypatch)
    plan = broker.plan(request(rig, action="commit", confirmed=confirmed))
    assert plan.destructive
    with pytest.raises(Conflict, match="Confirmation explicite requise"):
        broker.run(plan)
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert rows(storage) == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)


def test_explicit_canonical_broker_commit_retains_independent_origins(rig, monkeypatch):
    storage, _root, paths, pubs, _lock = rig
    backup(rig)
    broker = broker_for(rig, monkeypatch)
    plan = broker.plan(request(rig, action="commit", confirmed=True))
    assert plan.destructive and plan.facts["reason"] == "active_references"
    result = broker.run(plan)
    assert result.ok
    assert result.facts["status"] == "retired"
    assert result.facts["backup_relinked"] == 1
    assert result.facts["removed_file"] is True
    assert result.changed == 2
    assert storage.placement_get(pubs[1].placement_id) is None
    assert not paths[1].exists()
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES
    assert rows(storage) == (1, 2, 3, 3)


def test_canonical_legacy_delete_is_blocked_at_plan_and_stale_run(rig, monkeypatch):
    storage, _root, paths, pubs, _lock = rig
    broker = broker_for(rig, monkeypatch)
    legacy = LibraryMutationRequest(
        "delete_duplicate", placement_id=pubs[1].placement_id,
        keep_placement_id=pubs[0].placement_id,
    )
    with pytest.raises(Conflict, match="supervisé"):
        broker.plan(legacy)
    from cifereca.plugin_api import LibraryMutationPlan
    forged = LibraryMutationPlan(
        "delete_duplicate", "Stale historical plan", legacy, destructive=True,
    )
    with pytest.raises(Conflict, match="supervisé"):
        broker.run(forged)
    assert rows(storage) == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)


def test_forged_non_destructive_plan_cannot_request_destructive_run(rig, monkeypatch):
    storage, _root, paths, pubs, _lock = rig
    broker = broker_for(rig, monkeypatch)
    plan = broker.plan(request(rig, action="commit", confirmed=True))
    with pytest.raises(Conflict, match="Plan de dédoublonnage invalide"):
        broker.run(replace(plan, destructive=False))
    assert rows(storage) == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)


@pytest.mark.parametrize("invalid", [
    {}, {"action": "unlink"}, {"action": None}, {"action": True},
])
def test_invalid_or_ambiguous_broker_action_fails_closed(rig, monkeypatch, invalid):
    _storage, _root, paths, pubs, _lock = rig
    broker = broker_for(rig, monkeypatch)
    opts = invalid if invalid else {"action": "unlink"}
    with pytest.raises(Conflict, match="Option de dédoublonnage invalide"):
        broker.plan(LibraryMutationRequest(
            "canonical_dedup", placement_id=pubs[1].placement_id,
            keep_placement_id=pubs[0].placement_id, options=opts,
        ))
    assert all(path.read_bytes() == BYTES for path in paths)


def test_missing_configured_library_root_blocks_even_readonly_plan(rig, monkeypatch):
    storage, root, paths, pubs, _lock = rig
    broker = broker_for(rig, monkeypatch, root_override=root.parent / "elsewhere")
    with pytest.raises(Conflict, match="Racine de bibliothèque indisponible"):
        broker.plan(request(rig, action="commit", confirmed=True))
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert all(path.read_bytes() == BYTES for path in paths)


def test_pending_cleanup_requires_another_explicit_confirmed_recover(rig, monkeypatch):
    storage, _root, paths, pubs, _lock = rig
    broker = broker_for(rig, monkeypatch)
    original_unlink = Path.unlink

    def refuse(path, *args, **kwargs):
        if path == paths[1]:
            raise PermissionError("synthetic-unlink-failure")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", refuse)
        stage = broker.run(broker.plan(
            request(rig, action="commit", confirmed=True),
        ))
    assert stage.ok is False
    assert stage.facts["status"] == "cleanup_pending"
    assert storage.placement_get(pubs[1].placement_id)["state"] == "dedup_cleanup_pending"
    assert paths[1].read_bytes() == BYTES
    with pytest.raises(Conflict, match="Confirmation explicite requise"):
        broker.run(broker.plan(request(rig, action="recover")))
    recover = broker.run(broker.plan(
        request(rig, action="recover", confirmed=True),
    ))
    assert recover.ok is True
    assert recover.facts["status"] == "retired"
    assert storage.placement_get(pubs[1].placement_id) is None
    assert not paths[1].exists()
    assert rows(storage) == (1, 2, 3, 3)


def test_foreign_keeper_never_mints_right_or_deletes_file(rig, monkeypatch):
    storage, _root, paths, pubs, _lock = rig
    broker = broker_for(rig, monkeypatch)
    with pytest.raises(Conflict, match="Doublon canonique non vérifié"):
        broker.plan(request(
            rig, action="commit", confirmed=True,
            keeper=pubs[2].placement_id,
        ))
    assert rows(storage) == (1, 3, 3, 3)
    assert all(path.read_bytes() == BYTES for path in paths)
