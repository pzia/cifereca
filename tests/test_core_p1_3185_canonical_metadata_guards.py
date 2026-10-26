"""#3185: maintenance cannot orphan paths stored in canonical metadata.

Synthetic files and SQLite only; no source, network or DRM operations.
"""
from __future__ import annotations

import json

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.core.duplicate_maintenance_guard import canonical_metadata_mentions_path
from cifereca.plugin_api import Conflict, LibraryMutationRequest
from tests.test_core041_artifact_authority import make_service
from tests.test_core_p0_3185_duplicate_dependencies import _rig


def _mark(storage, table, path, *, keeper_id=None, artifact_id=None, raw=None):
    """Insert independent metadata evidence under an exact canonical table."""
    payload = raw if raw is not None else json.dumps({
        "active": {"book": {"paths": [str(path)]}},
    })
    if table == "entitlements":
        right = storage.entitlement_put({
            "provider_id": "kobo", "credential_id": "fixture-card",
            "identity_id": "id1", "account_id": "reader",
            "item_id": "separate-right", "entitlement_type": "owned",
            "state": "active", "metadata": {},
        })
        with storage._lock, storage._connect() as db:
            db.execute("UPDATE entitlements SET metadata_json=? WHERE entitlement_id=?",
                       (payload, right))
    elif table == "artifacts":
        with storage._lock, storage._connect() as db:
            db.execute("UPDATE artifacts SET metadata_json=? WHERE artifact_id=?",
                       (payload, artifact_id))
    elif table == "placements":
        with storage._lock, storage._connect() as db:
            db.execute("UPDATE placements SET metadata_json=? WHERE placement_id=?",
                       (payload, keeper_id))
    else:
        raise AssertionError("unrecognized fixture table")


@pytest.mark.parametrize("table", ["entitlements", "artifacts", "placements"])
def test_canonical_independent_metadata_blocks_duplicate_preview(tmp_path, table):
    service, broker, request, doomed, kept, artifact = _rig(tmp_path)
    try:
        _mark(service.storage, table, doomed, keeper_id=request.keep_placement_id,
              artifact_id=artifact.artifact_id)
        with pytest.raises(Conflict, match="référencé"):
            broker.plan(request)
        assert doomed.is_file() and kept.is_file()
        assert service.storage.placement_get(request.placement_id) is not None
    finally:
        service.storage.close()


@pytest.mark.parametrize("table", ["entitlements", "artifacts", "placements"])
def test_new_reference_after_preview_prevents_unlink(tmp_path, table):
    service, broker, request, doomed, kept, artifact = _rig(tmp_path)
    try:
        plan = broker.plan(request)
        _mark(service.storage, table, doomed, keeper_id=request.keep_placement_id,
              artifact_id=artifact.artifact_id)
        with pytest.raises(Conflict, match="référencé"):
            broker.run(plan)
        assert doomed.is_file() and kept.is_file()
        assert service.storage.placement_get(request.placement_id) is not None
    finally:
        service.storage.close()


@pytest.mark.parametrize("table", ["entitlements", "artifacts", "placements"])
def test_unclassifiable_canonical_metadata_refuses_duplicate(tmp_path, table):
    service, broker, request, doomed, kept, artifact = _rig(tmp_path)
    try:
        _mark(service.storage, table, kept, keeper_id=request.keep_placement_id,
              artifact_id=artifact.artifact_id, raw="{bad")
        with pytest.raises(Conflict):
            broker.plan(request)
        assert doomed.is_file() and kept.is_file()
    finally:
        service.storage.close()


@pytest.mark.parametrize("table", ["entitlements", "artifacts", "placements"])
def test_canonical_metadata_refuses_rename_before_database_commit(tmp_path, table):
    service, artifact, placement = make_service(tmp_path)
    old = tmp_path / "library" / "Book.epub"
    new = tmp_path / "library" / "Author - Book.epub"
    broker = LibraryMaintenanceBrokerImpl(service)
    plan = broker.plan(LibraryMutationRequest("normalize_filename", placement_id=placement))
    try:
        _mark(service.storage, table, old, keeper_id=placement,
              artifact_id=artifact.artifact_id)
        with pytest.raises(ValueError, match="relocate_canonical_metadata_requires_relink"):
            broker.run(plan)
        assert old.is_file() and not new.exists()
        assert service.storage.placement_get(placement)["path"] == str(old)
        assert service.storage.catalog_entries()[0]["metadata"]["path"] == str(old)
    finally:
        service.storage.close()


def test_healthy_metadata_does_not_block_unrelated_normalization(tmp_path):
    service, artifact, placement = make_service(tmp_path)
    old = tmp_path / "library" / "Book.epub"
    new = tmp_path / "library" / "Author - Book.epub"
    broker = LibraryMaintenanceBrokerImpl(service)
    try:
        _mark(service.storage, "entitlements", old.with_name("unrelated.epub"))
        _mark(service.storage, "artifacts", old.with_name("unrelated.epub"),
              artifact_id=artifact.artifact_id)
        _mark(service.storage, "placements", old.with_name("unrelated.epub"),
              keeper_id=placement)
        result = broker.run(broker.plan(
            LibraryMutationRequest("normalize_filename", placement_id=placement),
        ))
        assert result.ok and new.is_file() and not old.exists()
        assert service.storage.placement_get(placement)["path"] == str(new)
    finally:
        service.storage.close()


def test_scanner_rejects_unknown_table_without_issuing_select(tmp_path):
    service, _artifact, _placement = make_service(tmp_path)
    try:
        with service.storage._lock, service.storage._connect() as db:
            assert canonical_metadata_mentions_path(
                db, "/tmp/fixture", tables=("anything; DROP TABLE artifacts",)
            ) is None
            assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    finally:
        service.storage.close()
