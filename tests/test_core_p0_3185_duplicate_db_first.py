"""#3185: duplicate deletion commits SQLite before irreversible unlink.

All paths, byte strings, receipts and placements are synthetic and local.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.plugin_api import LibraryMutationRequest
from tests.test_core_p0_3185_duplicate_dependencies import _rig


def test_sqlite_commit_failure_leaves_both_files_and_all_placements(tmp_path, monkeypatch):
    service, broker, request, candidate, kept, _ = _rig(tmp_path)
    plan = broker.plan(request)

    def failed_delete(_placement_id):
        assert kept.read_bytes() == candidate.read_bytes() == b"book-data"
        raise RuntimeError("synthetic DB unavailable")

    monkeypatch.setattr(service.storage, "placement_delete", failed_delete)
    with pytest.raises(RuntimeError, match="synthetic DB unavailable"):
        broker.run(plan)
    assert candidate.read_bytes() == kept.read_bytes() == b"book-data"
    assert service.storage.placement_get(request.placement_id)["state"] == "present"


def test_uncatchable_exit_after_sqlite_does_not_leave_a_missing_active_placement(
    tmp_path, monkeypatch,
):
    service, broker, request, candidate, kept, _ = _rig(tmp_path)
    plan = broker.plan(request)
    original_unlink = Path.unlink

    def interrupt(path, *args, **kwargs):
        if path == candidate:
            raise SystemExit("synthetic process interrupted after SQL")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupt)
    with pytest.raises(SystemExit):
        broker.run(plan)
    assert candidate.read_bytes() == kept.read_bytes() == b"book-data"
    assert service.storage.placement_get(request.placement_id) is None
    assert service.storage.placement_get(request.keep_placement_id)["path"] == str(kept)


def test_cleanup_permission_denied_preserves_file_and_warns(tmp_path, monkeypatch):
    service, broker, request, candidate, kept, _ = _rig(tmp_path)
    plan = broker.plan(request)
    original_unlink = Path.unlink

    def reject(path, *args, **kwargs):
        if path == candidate:
            raise PermissionError("synthetic unlink denied")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", reject)
    outcome = broker.run(plan)
    assert outcome.ok and outcome.changed == 1
    assert outcome.warnings == (
        "Fichier doublon conservé : nettoyage local à vérifier.",
    )
    assert candidate.read_bytes() == kept.read_bytes() == b"book-data"
    assert service.storage.placement_get(request.placement_id) is None


@pytest.mark.parametrize("changed", ["candidate", "kept"])
def test_inode_changed_during_sqlite_commit_must_not_unlink_any_copy(
    tmp_path, monkeypatch, changed,
):
    service, broker, request, candidate, kept, _ = _rig(tmp_path)
    plan = broker.plan(request)
    original_delete = service.storage.placement_delete

    def committed_then_replaced(placement_id):
        original_delete(placement_id)
        selected = candidate if changed == "candidate" else kept
        replacement = selected.with_name("other-file.tmp")
        replacement.write_bytes(b"foreign-replacement")
        replacement.replace(selected)

    monkeypatch.setattr(service.storage, "placement_delete", committed_then_replaced)
    outcome = broker.run(plan)
    assert outcome.ok and outcome.warnings
    assert candidate.is_file() and kept.is_file()
    assert service.storage.placement_get(request.placement_id) is None
    assert service.storage.placement_get(request.keep_placement_id) is not None


def test_path_rebound_to_another_placement_after_commit_is_preserved(
    tmp_path, monkeypatch,
):
    service, broker, request, candidate, kept, artifact = _rig(tmp_path)
    plan = broker.plan(request)
    original_delete = service.storage.placement_delete

    def committed_then_rebound(placement_id):
        original_delete(placement_id)
        service.storage.placement_put({
            "path": str(candidate), "artifact_id": artifact.artifact_id,
            "identity_id": "other-identity", "provider_id": "local",
            "source_item_id": "other-edition", "state": "present",
            "metadata": {"source": "synthetic-new-owner"},
        })

    monkeypatch.setattr(service.storage, "placement_delete", committed_then_rebound)
    outcome = broker.run(plan)
    assert outcome.ok and outcome.warnings
    assert candidate.read_bytes() == kept.read_bytes() == b"book-data"
    rebound = service.storage.placement_for_path(candidate)
    # The stable path-derived placement ID may be reused by the new owner.
    # Its current source coordinates, not the historical ID, are authoritative.
    assert rebound is not None and rebound["identity_id"] == "other-identity"
    assert rebound["source_item_id"] == "other-edition"


def test_normal_duplicate_deletion_still_removes_unreferenced_physical_copy(tmp_path):
    service, broker, request, candidate, kept, _ = _rig(tmp_path)
    outcome = broker.run(broker.plan(request))
    assert outcome.ok and outcome.changed == 1 and outcome.warnings == ()
    assert not candidate.exists() and kept.read_bytes() == b"book-data"
    assert service.storage.placement_get(request.placement_id) is None
