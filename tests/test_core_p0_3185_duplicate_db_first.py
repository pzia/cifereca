"""#3185: duplicate deletion commits SQLite before irreversible unlink.

All paths, byte strings, receipts and placements are synthetic and local.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca import artifact_runtime
from cifereca.plugin_api import Conflict, LibraryMutationRequest
from tests.test_core_p0_3185_duplicate_dependencies import _rig


def test_sqlite_commit_failure_leaves_both_files_and_all_placements(tmp_path, monkeypatch):
    service, broker, request, candidate, kept, _ = _rig(tmp_path)
    plan = broker.plan(request)

    def failed_delete(**_coordinates):
        assert kept.read_bytes() == candidate.read_bytes() == b"book-data"
        raise RuntimeError("synthetic DB unavailable")

    monkeypatch.setattr(service.storage, "placement_retire_duplicate_atomic", failed_delete)
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
    original_delete = service.storage.placement_retire_duplicate_atomic

    def committed_then_replaced(**coordinates):
        original_delete(**coordinates)
        selected = candidate if changed == "candidate" else kept
        replacement = selected.with_name("other-file.tmp")
        replacement.write_bytes(b"foreign-replacement")
        replacement.replace(selected)

    monkeypatch.setattr(service.storage, "placement_retire_duplicate_atomic", committed_then_replaced)
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
    original_delete = service.storage.placement_retire_duplicate_atomic

    def committed_then_rebound(**coordinates):
        original_delete(**coordinates)
        service.storage.placement_put({
            "path": str(candidate), "artifact_id": artifact.artifact_id,
            "identity_id": "other-identity", "provider_id": "local",
            "source_item_id": "other-edition", "state": "present",
            "metadata": {"source": "synthetic-new-owner"},
        })

    monkeypatch.setattr(service.storage, "placement_retire_duplicate_atomic", committed_then_rebound)
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


def test_new_catalogue_dependency_during_hash_blocks_atomic_delete(tmp_path, monkeypatch):
    service, broker, request, candidate, kept, artifact = _rig(tmp_path)
    plan = broker.plan(request)
    original_hash = artifact_runtime.sha256_file
    inserted = False

    def attach_after_precheck(path):
        nonlocal inserted
        if path == candidate and not inserted:
            inserted = True
            service.storage.catalog_entry_put({
                "artifact_id": artifact.artifact_id,
                "placement_id": request.placement_id,
                "title": "New active notice", "identity_id": "id1",
                "provider_id": "local", "item_id": "book-1",
                "local_state": "local", "metadata": {"path": str(candidate)},
            })
        return original_hash(path)

    monkeypatch.setattr(artifact_runtime, "sha256_file", attach_after_precheck)
    try:
        with pytest.raises(Conflict, match="Le doublon n\u0027est plus vérifiable"):
            broker.run(plan)
        assert inserted
        assert candidate.read_bytes() == kept.read_bytes() == b"book-data"
        assert service.storage.placement_get(request.placement_id) is not None
        assert any(row.get("placement_id") == request.placement_id
                   for row in service.storage.catalog_entries())
    finally:
        service.storage.close()


def test_keeper_owner_changed_during_hash_blocks_atomic_delete(tmp_path, monkeypatch):
    service, broker, request, candidate, kept, _artifact = _rig(tmp_path)
    plan = broker.plan(request)
    original_hash = artifact_runtime.sha256_file
    changed = False

    def rebind_keeper_after_precheck(path):
        nonlocal changed
        if path == candidate and not changed:
            changed = True
            with service.storage._lock, service.storage._connect() as db:
                db.execute(
                    "UPDATE placements SET identity_id=? WHERE placement_id=?",
                    ("different-owner", request.keep_placement_id),
                )
        return original_hash(path)

    monkeypatch.setattr(artifact_runtime, "sha256_file", rebind_keeper_after_precheck)
    try:
        with pytest.raises(Conflict, match="Le doublon n\u0027est plus vérifiable"):
            broker.run(plan)
        assert changed
        assert candidate.is_file() and kept.is_file()
        assert service.storage.placement_get(request.placement_id) is not None
        assert service.storage.placement_get(request.keep_placement_id)["identity_id"] == "different-owner"
    finally:
        service.storage.close()


def _assign_fictitious_provider(service, request, provider):
    # The fixture starts with a local placement. Both copies and the retained
    # catalogue now share a fictitious provider; no real provider I/O occurs.
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "UPDATE placements SET provider_id=? WHERE placement_id IN (?,?)",
            (provider, request.placement_id, request.keep_placement_id),
        )
        db.execute(
            "UPDATE catalog_entries SET provider_id=? WHERE placement_id=?",
            (provider, request.keep_placement_id),
        )


def _put_right(service, provider, borrower):
    service.storage.entitlement_put({
        "provider_id": provider, "identity_id": "id1",
        "credential_id": "credential-" + borrower,
        "account_id": borrower, "item_id": "book-1",
        "entitlement_type": "owned", "state": "active",
        "metadata": {},
    })


def test_unknown_provider_multiple_right_owners_block_preview(tmp_path):
    service, broker, request, candidate, kept, _artifact = _rig(tmp_path)
    try:
        provider = "synthetic-loan-provider"
        _assign_fictitious_provider(service, request, provider)
        _put_right(service, provider, "first")
        _put_right(service, provider, "second")
        with pytest.raises(Conflict):
            broker.plan(request)
        assert candidate.is_file() and kept.is_file()
        assert service.storage.placement_get(request.placement_id) is not None
    finally:
        service.storage.close()


def test_unknown_provider_second_owner_during_hash_blocks_commit(tmp_path, monkeypatch):
    service, broker, request, candidate, kept, _artifact = _rig(tmp_path)
    try:
        provider = "synthetic-loan-provider"
        _assign_fictitious_provider(service, request, provider)
        _put_right(service, provider, "first")
        plan = broker.plan(request)
        original_hash = artifact_runtime.sha256_file
        inserted = False

        def attach_second_owner(path):
            nonlocal inserted
            if path == candidate and not inserted:
                inserted = True
                _put_right(service, provider, "second")
            return original_hash(path)

        monkeypatch.setattr(artifact_runtime, "sha256_file", attach_second_owner)
        with pytest.raises(Conflict, match="Le doublon n'est plus vérifiable"):
            broker.run(plan)
        assert inserted
        assert candidate.is_file() and kept.is_file()
        assert service.storage.placement_get(request.placement_id) is not None
        assert len(service.storage.entitlements(provider_id=provider)) == 2
    finally:
        service.storage.close()


def test_unscoped_placement_with_provider_right_is_not_deletable(tmp_path):
    service, broker, request, candidate, kept, _artifact = _rig(tmp_path)
    try:
        provider = "synthetic-loan-provider"
        _assign_fictitious_provider(service, request, provider)
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "UPDATE placements SET source_item_id=NULL WHERE placement_id IN (?,?)",
                (request.placement_id, request.keep_placement_id),
            )
        _put_right(service, provider, "first")
        with pytest.raises(Conflict):
            broker.plan(request)
        assert candidate.is_file() and kept.is_file()
        assert service.storage.placement_get(request.placement_id) is not None
    finally:
        service.storage.close()


def _insert_unattributed_legacy_right(service, *, provider, item="book-1"):
    service.storage.entitlement_put({
        "provider_id": provider,
        "credential_id": "orphan-credential",
        "account_id": "reader",
        "identity_id": None,
        "item_id": item,
        "entitlement_type": "owned", "state": "active",
        "metadata": {},
    })


def test_legacy_null_identity_right_blocks_unknown_provider_preview(tmp_path):
    service, broker, request, candidate, kept, _artifact = _rig(tmp_path)
    try:
        provider = "synthetic-legacy-provider"
        _assign_fictitious_provider(service, request, provider)
        _insert_unattributed_legacy_right(service, provider=provider)
        with pytest.raises(Conflict):
            broker.plan(request)
        assert candidate.is_file() and kept.is_file()
        assert service.storage.placement_get(request.placement_id) is not None
    finally:
        service.storage.close()


def test_legacy_null_identity_right_inserted_during_hash_blocks_commit(tmp_path, monkeypatch):
    service, broker, request, candidate, kept, _artifact = _rig(tmp_path)
    try:
        provider = "synthetic-legacy-provider"
        _assign_fictitious_provider(service, request, provider)
        plan = broker.plan(request)
        original_hash = artifact_runtime.sha256_file
        inserted = False

        def introduce_legacy_right(path):
            nonlocal inserted
            if path == candidate and not inserted:
                inserted = True
                _insert_unattributed_legacy_right(service, provider=provider)
            return original_hash(path)

        monkeypatch.setattr(artifact_runtime, "sha256_file", introduce_legacy_right)
        with pytest.raises(Conflict, match="Le doublon n'est plus vérifiable"):
            broker.run(plan)
        assert inserted
        assert candidate.is_file() and kept.is_file()
        assert service.storage.placement_get(request.placement_id) is not None
    finally:
        service.storage.close()


def test_legacy_null_identity_other_edition_does_not_block(tmp_path):
    service, broker, request, candidate, kept, _artifact = _rig(tmp_path)
    try:
        provider = "synthetic-legacy-provider"
        _assign_fictitious_provider(service, request, provider)
        _insert_unattributed_legacy_right(service, provider=provider, item="unrelated-edition")
        result = broker.run(broker.plan(request))
        assert result.ok and result.changed == 1
        assert not candidate.exists() and kept.is_file()
    finally:
        service.storage.close()

@pytest.mark.parametrize("unknown_edition", [None, ""])
def test_duplicate_without_recorded_edition_cannot_be_retired_even_without_rights(
    tmp_path, unknown_edition,
):
    service, broker, request, candidate, kept, artifact = _rig(tmp_path)
    try:
        # Matching SHA, identity and provider alone cannot establish that
        # two manual/imported copies have interchangeable provenance.
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "UPDATE placements SET source_item_id=? WHERE placement_id IN (?,?)",
                (unknown_edition, request.placement_id, request.keep_placement_id),
            )
        with pytest.raises(Conflict):
            broker.plan(request)
        # The storage primitive is also fail-closed if invoked directly
        # without the broker's preflight.
        with pytest.raises(ValueError, match="duplicate_retire_scope_invalid"):
            service.storage.placement_retire_duplicate_atomic(
                placement_id=request.placement_id,
                keep_placement_id=request.keep_placement_id,
                candidate_path=str(candidate),
                kept_path=str(kept),
                artifact_id=artifact.artifact_id,
                identity_id="id1", provider_id="local",
                source_item_id=unknown_edition,
            )
        assert candidate.read_bytes() == kept.read_bytes() == b"book-data"
        assert service.storage.placement_get(request.placement_id) is not None
        assert service.storage.placement_get(request.keep_placement_id) is not None
    finally:
        service.storage.close()
