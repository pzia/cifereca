"""P0 #3185: duplicate maintenance must not orphan live path references.

A matching SHA and right scope are necessary but insufficient. These offline
fixtures use real SQLite and files; no supplier or DRM operations.
"""
from __future__ import annotations

import json
import shutil

import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.core.library_declared_links import _PREFIX
from cifereca.plugin_api import Conflict, LibraryMutationRequest
from tests.test_core041_artifact_authority import make_service


def _rig(tmp_path):
    service, artifact, kept_id = make_service(tmp_path)
    kept = tmp_path / "library" / "Book.epub"
    doomed = tmp_path / "local-second.epub"
    shutil.copy2(kept, doomed)
    doomed_id = service.storage.placement_put({
        "artifact_id": artifact.artifact_id, "path": str(doomed),
        "identity_id": "id1", "provider_id": "local",
        "source_item_id": "book-1", "state": "present", "metadata": {},
    })
    broker = LibraryMaintenanceBrokerImpl(service)
    req = LibraryMutationRequest(
        "delete_duplicate", placement_id=doomed_id,
        keep_placement_id=kept_id,
    )
    return service, broker, req, doomed, kept, artifact


@pytest.mark.parametrize("dependency", [
    "catalog", "backup", "acquisition", "declaration",
])
def test_active_reference_forbids_duplicate_deletion(tmp_path, dependency):
    service, broker, req, doomed, kept, artifact = _rig(tmp_path)
    try:
        if dependency == "catalog":
            service.storage.catalog_entry_put({
                "artifact_id": artifact.artifact_id,
                "placement_id": req.placement_id,
                "title": "Private candidate", "identity_id": "id1",
                "provider_id": "local", "item_id": "book-1",
                "local_state": "local", "metadata": {"path": str(doomed)},
            })
        elif dependency == "backup":
            service.storage.backup_put({
                "provider_id": "kobo", "credential_id": "one",
                "identity_id": "id1", "item_id": "book-1",
                "account_id": "reader", "title": "Private",
                "local_path": str(doomed), "status": "complete",
                "sha256": artifact.sha256, "size": doomed.stat().st_size,
            })
        elif dependency == "acquisition":
            service.storage.acquisition_put({
                "provider_id": "numilog", "credential_id": "one",
                "identity_id": "id1", "account_id": "reader",
                "item_id": "book-1", "status": "index_pending",
                "metadata": {"stage": {"path": str(doomed)}},
            })
        elif dependency == "declaration":
            relation = "a" * 32
            record = {
                "version": 1, "relation_id": relation,
                "scope": {
                    "identity_id": "id1", "provider_id": "kobo",
                    "credential_id": "one", "account_id": "reader",
                    "item_id": "book-1",
                },
                "file": {
                    "path": str(doomed), "sha256": artifact.sha256,
                    "size": doomed.stat().st_size,
                    "fingerprint": [1, 2, 3, 4, 5],
                },
                "right_state": {
                    "entitlement_id": "owned-one", "entitlement_type": "owned",
                    "state": "active", "loan_id": None, "expires_at": None,
                },
                "state": "active", "origin": "user_declaration",
                "created_at": 1, "revoked_at": None,
            }
            service.storage.metadata_set(_PREFIX + relation, json.dumps(record))
        with pytest.raises(Conflict):
            broker.plan(req)
        assert doomed.exists() and kept.exists()
        assert service.storage.placement_get(req.placement_id) is not None
    finally:
        service.storage.close()


@pytest.mark.parametrize("encoding", ["literal", "nested", "escaped"])
def test_generic_metadata_path_reference_blocks_duplicate_preview(
    tmp_path, encoding,
):
    service, broker, req, doomed, kept, _artifact = _rig(tmp_path)
    try:
        if encoding == "literal":
            value = str(doomed)
        elif encoding == "nested":
            value = json.dumps({"resume": {"path": str(doomed)}})
        else:
            value = json.dumps({"resume": {"path": str(doomed)}}).replace(
                "/", chr(92) + "u002f",
            )
        service.storage.metadata_set("sync.private.staged-v1", value)
        with pytest.raises(Conflict):
            broker.plan(req)
        assert doomed.is_file() and kept.is_file()
        assert service.storage.placement_get(req.placement_id) is not None
    finally:
        service.storage.close()


def test_metadata_path_added_after_preview_blocks_sqlite_retirement(
    tmp_path,
):
    service, broker, req, doomed, kept, _artifact = _rig(tmp_path)
    try:
        plan = broker.plan(req)
        service.storage.metadata_set(
            "sync.private.staged-v1",
            json.dumps({"resume": {"path": str(doomed)}}),
        )
        with pytest.raises(Conflict):
            broker.run(plan)
        assert doomed.is_file() and kept.is_file()
        assert service.storage.placement_get(req.placement_id) is not None
    finally:
        service.storage.close()


def test_external_reference_appearing_after_preview_prevents_unlink(tmp_path):
    service, broker, req, doomed, kept, artifact = _rig(tmp_path)
    try:
        plan = broker.plan(req)
        service.storage.acquisition_put({
            "provider_id": "numilog", "credential_id": "one",
            "identity_id": "id1", "item_id": "book-1",
            "status": "index_pending", "metadata": {"path": str(doomed)},
        })
        with pytest.raises(Conflict):
            broker.run(plan)
        assert doomed.is_file() and kept.is_file()
        assert service.storage.placement_get(req.placement_id) is not None
    finally:
        service.storage.close()


def test_candidate_bytes_changed_since_plan_are_never_unlinked(tmp_path):
    service, broker, req, doomed, kept, artifact = _rig(tmp_path)
    try:
        plan = broker.plan(req)
        doomed.write_bytes(b"PRIVATE different content")
        with pytest.raises(Conflict):
            broker.run(plan)
        assert doomed.is_file() and kept.is_file()
        assert service.storage.placement_get(req.placement_id) is not None
    finally:
        service.storage.close()


def test_symlink_candidate_is_not_unlinked_after_preview(tmp_path):
    service, broker, req, doomed, kept, artifact = _rig(tmp_path)
    try:
        plan = broker.plan(req)
        doomed.unlink()
        doomed.symlink_to(kept)
        with pytest.raises(Conflict):
            broker.run(plan)
        assert doomed.is_symlink() and kept.is_file()
        assert service.storage.placement_get(req.placement_id) is not None
    finally:
        service.storage.close()


def test_normal_unreferenced_duplicate_can_still_be_removed(tmp_path):
    service, broker, req, doomed, kept, artifact = _rig(tmp_path)
    try:
        plan = broker.plan(req)
        outcome = broker.run(plan)
        assert outcome.ok and not doomed.exists() and kept.exists()
        assert service.storage.placement_get(req.placement_id) is None
    finally:
        service.storage.close()
