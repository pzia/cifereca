"""#2789: crash-consistent, provenance-checked filename normalisation.

Every path and right is fictitious and local. The mutation is explicitly
requested through the existing Core maintenance broker, never through an
automatic audit/reconciliation action.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import LibraryMutationRequest
from tests.test_core041_artifact_authority import Control, make_service


def _case(tmp_path):
    service, source, old_id = make_service(tmp_path)
    old = tmp_path / "library" / "Book.epub"
    new = tmp_path / "library" / "Author - Book.epub"
    broker = LibraryMaintenanceBrokerImpl(service)
    plan = broker.plan(
        LibraryMutationRequest("normalize_filename", placement_id=old_id),
    )
    return service, old_id, broker, plan, old, new, source


def test_normalisation_keeps_old_path_until_sqlite_commits(tmp_path):
    service, old_id, broker, plan, old, new, _ = _case(tmp_path)
    original = service.storage.relocate_local_path
    seen = []

    def transactional_relocation(*args, **kwargs):
        # Crash at this exact point: SQLite still points at an existing old
        # name AND the destination already exists as the same verified inode.
        assert old.is_file() and new.is_file()
        assert os.stat(old).st_ino == os.stat(new).st_ino
        assert service.storage.placement_get(old_id)["path"] == str(old)
        seen.append(True)
        return original(*args, **kwargs)

    with patch.object(service.storage, "relocate_local_path",
                      side_effect=transactional_relocation):
        outcome = broker.run(plan, Control())
    assert seen == [True]
    assert outcome.ok and outcome.changed == 1 and not outcome.warnings
    assert not old.exists() and new.read_bytes() == b"book-data"
    assert service.storage.placement_get(old_id)["path"] == str(new)
    assert new.stat().st_nlink == 1


def test_failed_sqlite_transaction_keeps_original_and_removes_only_own_link(tmp_path):
    service, old_id, broker, plan, old, new, _ = _case(tmp_path)
    with patch.object(service.storage, "relocate_local_path",
                      side_effect=RuntimeError("PRIVATE_sql_failure")):
        with pytest.raises(RuntimeError, match="PRIVATE_sql_failure"):
            broker.run(plan, Control())
    assert old.read_bytes() == b"book-data"
    assert not new.exists()
    assert old.stat().st_nlink == 1
    assert service.storage.placement_get(old_id)["path"] == str(old)


def test_uncatchable_interrupt_before_sqlite_leaves_both_names_and_old_db(tmp_path):
    service, old_id, broker, plan, old, new, _ = _case(tmp_path)
    with patch.object(service.storage, "relocate_local_path",
                      side_effect=SystemExit("synthetic_process_exit")):
        with pytest.raises(SystemExit):
            broker.run(plan, Control())
    assert old.read_bytes() == new.read_bytes() == b"book-data"
    assert service.storage.placement_get(old_id)["path"] == str(old)


def test_uncatchable_interrupt_after_sqlite_leaves_both_names_and_new_db(
    tmp_path, monkeypatch,
):
    service, old_id, broker, plan, old, new, _ = _case(tmp_path)
    original = Path.unlink

    def interrupt(path, *args, **kwargs):
        if path == old:
            raise SystemExit("synthetic_exit_after_commit")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupt)
    with pytest.raises(SystemExit):
        broker.run(plan, Control())
    assert old.is_file() and new.is_file()
    assert old.read_bytes() == new.read_bytes() == b"book-data"
    assert service.storage.placement_get(old_id)["path"] == str(new)


def test_existing_attested_target_inode_is_preserved_and_old_is_retired(tmp_path):
    service, old_id, broker, plan, old, new, source = _case(tmp_path)
    new.write_bytes(old.read_bytes())
    former_ino = new.stat().st_ino
    target_id = service.storage.placement_put({
        "artifact_id": source.artifact_id,
        "path": str(new),
        "identity_id": "id1",
        "provider_id": "local",
        "source_item_id": "book-1",
        "state": "present",
        "metadata": {},
    })
    result = broker.run(plan, Control())
    assert result.ok and result.changed == 1
    assert new.stat().st_ino == former_ino
    assert not old.exists()
    assert service.storage.placement_get(old_id) is None
    assert service.storage.placement_get(target_id)["path"] == str(new)


def test_indexed_but_physically_absent_target_is_recreated_safely(tmp_path):
    service, old_id, broker, plan, old, new, source = _case(tmp_path)
    target_id = service.storage.placement_put({
        "artifact_id": source.artifact_id,
        "path": str(new),
        "identity_id": "id1",
        "provider_id": "local",
        "source_item_id": "book-1",
        "state": "missing",
        "metadata": {},
    })
    outcome = broker.run(plan, Control())
    assert outcome.ok
    assert not old.exists() and new.read_bytes() == b"book-data"
    assert service.storage.placement_get(old_id) is None
    assert service.storage.placement_get(target_id)["path"] == str(new)


def test_link_creation_failure_does_not_change_source_or_sqlite(tmp_path):
    service, old_id, broker, plan, old, new, _ = _case(tmp_path)
    with patch("cifereca.artifact_runtime.os.link",
               side_effect=OSError("synthetic_fs_refusal")):
        with pytest.raises(OSError, match="synthetic_fs_refusal"):
            broker.run(plan, Control())
    assert old.read_bytes() == b"book-data"
    assert not new.exists()
    assert service.storage.placement_get(old_id)["path"] == str(old)


def test_cleanup_failure_is_reported_without_losing_sqlite_named_file(
    tmp_path, monkeypatch,
):
    service, old_id, broker, plan, old, new, _ = _case(tmp_path)
    original = Path.unlink

    def refuse(path, *args, **kwargs):
        if path == old:
            raise PermissionError("synthetic_permission")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    outcome = broker.run(plan, Control())
    assert outcome.ok and outcome.changed == 1
    assert outcome.warnings == (
        "Ancien nom conservé : nettoyage local à vérifier.",
    )
    assert old.is_file() and new.is_file()
    assert service.storage.placement_get(old_id)["path"] == str(new)


def test_symlink_destination_is_never_followed_or_committed(tmp_path):
    from cifereca.plugin_api import Conflict

    service, old_id, broker, plan, old, new, _ = _case(tmp_path)
    outside = tmp_path / "PRIVATE_OUTSIDE.epub"
    outside.write_bytes(old.read_bytes())
    new.symlink_to(outside)
    before = outside.read_bytes()
    with pytest.raises(Conflict):
        broker.run(plan, Control())
    assert old.read_bytes() == b"book-data"
    assert new.is_symlink() and outside.read_bytes() == before
    assert service.storage.placement_get(old_id)["path"] == str(old)
