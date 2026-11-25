"""#3185: renaming must not strand reconciliation/acquisition checkpoints.

All files, credentials, purchases and paths are synthetic and offline.
"""
from __future__ import annotations

import json

import pytest

from tests.test_core_p0_3171_atomic_publication_v14 import store

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


@pytest.mark.parametrize("encoding", ["literal", "json", "escaped", "unknown_escape"])
def test_generic_metadata_checkpoint_blocks_relocation_without_stranding_path(
    tmp_path, encoding,
):
    service, placement_id, old, new, broker, plan = _case(tmp_path)
    key = "synthetic.checkpoint"
    value = {
        "literal": str(old),
        "json": json.dumps({"resume": [str(old)]}),
        "escaped": json.dumps({"resume": [str(old)]}).replace("/", r"\/"),
        "unknown_escape": r"invalid\u",
    }[encoding]
    service.storage.metadata_set(key, value)
    with pytest.raises(ValueError, match="relocate_generic_metadata_requires_relink"):
        broker.run(plan, Control())
    _assert_unchanged(service, placement_id, old, new)
    assert service.storage.metadata_get(key) == value


@pytest.mark.parametrize("case", [
    "success", "legacy_call", "profile_relative", "wrong_root", "foreign_owner",
    "distinct_inode", "checkpoint", "target_placement",
])
def test_classification_relative_path_requires_exact_scoped_staged_move(
    store, tmp_path, case,
):
    """The common SQL writer migrates the scan's actual library-relative field.

    A fixture stages the physical name; production execution remains gated.
    """
    import os
    from tests.test_core_p0_3171_atomic_publication_v14 import register, rows
    from tests.test_core_p0_3176_organizer_review import _put_epub

    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    register(store, "B", "seller", "card-B")
    old, pid = _put_epub(store, root, "Acquisitions/Autrice - Roman commun.epub",
                         provider_id="local")
    new = root / ("B" if case == "foreign_owner" else "A") / "Livres/Autrice" / old.name
    new.parent.mkdir(parents=True)
    data = old.read_bytes()
    if case == "distinct_inode":
        new.write_bytes(data)
    else:
        os.link(old, new)
    relative = str(old.relative_to(root / "A" if case == "profile_relative" else root))
    with store._lock, store._connect() as db:
        db.execute("UPDATE placements SET metadata_json=? WHERE placement_id=?",
                   (json.dumps({"relative_path": relative, "artifact_kind": "file"}), pid))
    if case == "checkpoint":
        store.acquisition_put({
            "provider_id": "local", "credential_id": "fixture-account",
            "identity_id": "A", "item_id": "fixture-checkpoint",
            "status": "attention_required",
            "metadata": {"phase": "index_pending", "path": str(old)},
        })
    if case == "target_placement":
        store.placement_put({"identity_id": "A", "provider_id": "local",
                             "state": "missing", "path": str(new)})
    baseline, history, counts = store.placement_get(pid), store.acquisitions(), rows(store)
    before_catalog = store.catalog_entries(placement_id=pid)
    kwargs = {} if case == "legacy_call" else {
        "classification_root": tmp_path if case == "wrong_root" else root,
    }
    if case == "success":
        store.relocate_local_path(old, new, identity_id="A", provider_id="local", **kwargs)
        current = store.placement_get(pid)
        assert current["path"] == str(new)
        assert current["metadata"] == {
            "relative_path": str(new.relative_to(root)), "artifact_kind": "file",
        }
        for field in ("artifact_id", "identity_id", "provider_id", "source_item_id", "state"):
            assert current[field] == baseline[field]
        assert store.placement_for_path(old) is None
        assert all(e["metadata"]["path"] == str(new)
                   for e in store.catalog_entries(placement_id=pid))
    else:
        with pytest.raises(ValueError, match="relocate_"):
            store.relocate_local_path(old, new, identity_id="A", provider_id="local", **kwargs)
        assert store.placement_get(pid) == baseline
        assert store.catalog_entries(placement_id=pid) == before_catalog
    assert old.read_bytes() == new.read_bytes() == data
    assert rows(store) == counts and store.acquisitions() == history
