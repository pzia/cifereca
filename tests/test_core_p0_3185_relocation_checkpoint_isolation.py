"""#3185: renaming must not strand reconciliation/acquisition checkpoints.

All files, credentials, purchases and paths are synthetic and offline.
"""
from __future__ import annotations

import json

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.plugin_api import LibraryMutationRequest
from tests.test_core041_artifact_authority import Control, make_service


def _case(tmp_path):
    service, _, placement_id = make_service(tmp_path)
    old = tmp_path / "library" / "Book.epub"
    new = tmp_path / "library" / "Author - Book.epub"
    broker = LibraryMaintenanceBrokerImpl(service)
    plan = broker.plan(
        LibraryMutationRequest("normalize_filename", placement_id=placement_id)
    )
    return service, placement_id, old, new, broker, plan


def _assert_unchanged(service, placement_id, old, new):
    assert old.read_bytes() == b"book-data"
    assert not new.exists()
    assert service.storage.placement_get(placement_id)["path"] == str(old)
    assert all(
        entry["metadata"].get("path") == str(old)
        for entry in service.storage.catalog_entries()
        if entry.get("placement_id") == placement_id
    )


@pytest.mark.parametrize("status, metadata", [
    ("attention_required", {"kind": "numilog_delivered_artifact_v1",
                            "phase": "index_pending", "path": "$PATH"}),
    ("complete", {"path": "$PATH", "phase": "indexed"}),
    ("attention_required", {"checkpoint": {"output": ["$PATH"]}}),
])
def test_checkpoint_path_blocks_filename_mutation_without_stranding_sqlite(
    tmp_path, status, metadata,
):
    service, placement_id, old, new, broker, plan = _case(tmp_path)
    def substitute(value):
        if isinstance(value, str):
            return str(old) if value == "$PATH" else value
        if isinstance(value, dict):
            return {k: substitute(v) for k, v in value.items()}
        if isinstance(value, list):
            return [substitute(v) for v in value]
        return value
    service.storage.acquisition_put({
        "provider_id": "numilog", "credential_id": "synthetic-account",
        "identity_id": "id1", "item_id": "synthetic-edition",
        "status": status, "metadata": substitute(metadata),
    })
    with pytest.raises(ValueError, match="relocate_acquisition_reference_requires_relink"):
        broker.run(plan, Control())
    _assert_unchanged(service, placement_id, old, new)


def _deep_json():
    value = "not-the-path"
    for _ in range(27):
        value = [value]
    return json.dumps(value)


@pytest.mark.parametrize("metadata_json", [
    "{invalid json",
    '["unknown-structure"]',
    _deep_json(),
    " " * (1024 * 1024 + 1),
])
def test_unknown_acquisition_context_fails_closed(tmp_path, metadata_json):
    service, placement_id, old, new, broker, plan = _case(tmp_path)
    service.storage.acquisition_put({
        "provider_id": "numilog", "credential_id": "synthetic-account",
        "identity_id": "id1", "item_id": "synthetic-edition",
        "status": "attention_required", "metadata": {},
    })
    with service.storage._lock, service.storage._connect() as db:
        db.execute("UPDATE acquisition_ledger SET metadata_json=?", (metadata_json,))
    with pytest.raises(ValueError, match="relocate_acquisition_context_unavailable|relocate_acquisition_reference_requires_relink"):
        broker.run(plan, Control())
    _assert_unchanged(service, placement_id, old, new)


def _backup(service, old, provider, identity):
    with service.storage._lock, service.storage._connect() as db:
        db.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,title,"
            "status,updated_at,local_path,identity_id) VALUES(?,?,?,?,?,?,?,?)",
            (provider, "synthetic", "synthetic-edition", "Synthetic title",
             "complete", 1, str(old), identity),
        )


@pytest.mark.parametrize("provider, identity", [
    ("other-provider", "id1"),
    ("local", "other-identity"),
    ("local", None),
    ("local", ""),
])
def test_cross_scope_backup_cannot_be_orphaned(tmp_path, provider, identity):
    service, placement_id, old, new, broker, plan = _case(tmp_path)
    _backup(service, old, provider, identity)
    with pytest.raises(ValueError, match="relocate_scope_conflict"):
        broker.run(plan, Control())
    _assert_unchanged(service, placement_id, old, new)


def test_same_scope_backup_is_an_immutable_archive_after_canonical_rename(tmp_path):
    service, placement_id, old, new, broker, plan = _case(tmp_path)
    _backup(service, old, "local", "id1")
    with service.storage._lock, service.storage._connect() as db:
        # A successful normalization must not enter the historical writer,
        # even when a same-profile legacy receipt matches the source path.
        db.execute(
            "CREATE TRIGGER deny_backup_path_rewrite BEFORE UPDATE OF local_path "
            "ON backup_items BEGIN SELECT RAISE(ABORT, 'archive_is_immutable'); END"
        )
        before = db.execute(
            "SELECT local_path,updated_at,identity_id FROM backup_items "
            "WHERE provider_id='local'"
        ).fetchone()
    outcome = broker.run(plan, Control())
    assert outcome.ok and outcome.changed == 1
    assert not old.exists() and new.read_bytes() == b"book-data"
    assert service.storage.placement_get(placement_id)["path"] == str(new)
    with service.storage._lock, service.storage._connect() as db:
        assert db.execute(
            "SELECT local_path,updated_at,identity_id FROM backup_items "
            "WHERE provider_id='local'"
        ).fetchone() == before == (str(old), 1, "id1")


@pytest.mark.parametrize("count", [1, 2])
def test_relocation_checkpoint_scan_budget_never_authorizes_unseen_rows(
    tmp_path, monkeypatch, count,
):
    from cifereca import storage as storage_module

    service, placement_id, old, new, broker, plan = _case(tmp_path)
    monkeypatch.setattr(storage_module, "_MAX_RELOCATION_ACQUISITION_ROWS", 1)
    for index in range(count):
        service.storage.acquisition_put({
            "provider_id": "synthetic", "credential_id": "synthetic-account",
            "identity_id": "id1", "item_id": f"unrelated-{index}",
            "status": "attention_required", "metadata": {"phase": "index_pending"},
        })
    with service.storage._lock, service.storage._connect() as db:
        prior = db.execute("SELECT * FROM acquisition_ledger").fetchall()
    assert len(prior) == count
    if count == 1:
        result = broker.run(plan, Control())
        assert result.ok and result.changed == 1
        assert new.read_bytes() == b"book-data" and not old.exists()
        assert service.storage.placement_get(placement_id)["path"] == str(new)
    else:
        with pytest.raises(ValueError, match="relocate_acquisition_context_unavailable"):
            broker.run(plan, Control())
        _assert_unchanged(service, placement_id, old, new)
    with service.storage._lock, service.storage._connect() as db:
        assert db.execute("SELECT * FROM acquisition_ledger").fetchall() == prior
