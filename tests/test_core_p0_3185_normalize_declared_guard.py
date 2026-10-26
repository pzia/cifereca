"""P0 #3185: filename normalization must not strand a declared file path.

A user declaration is persisted independently of placements and carries a
path + fingerprint; mutating only placements is unsafe. This is a fail-closed
tranche until the fully transactional relocation is implemented.
"""
from __future__ import annotations

import json
import pytest

from cifereca.artifact_runtime import LibraryMaintenanceBrokerImpl
from cifereca.core.library_declared_links import _PREFIX
from cifereca.plugin_api import LibraryMutationRequest
from tests.test_core041_artifact_authority import make_service


def _declare(storage, artifact, old, *, state="active", relation="a" * 32):
    row = {
        "version": 1, "relation_id": relation,
        "scope": {
            "identity_id": "id1", "provider_id": "kobo",
            "credential_id": "vault-1", "account_id": "reader-1",
            "item_id": "book-1",
        },
        "file": {
            "path": str(old), "sha256": artifact.sha256,
            "size": old.stat().st_size if old.exists() else artifact.size_bytes,
            "fingerprint": [1, 2, 3, 4, 5],
        },
        "right_state": {
            "entitlement_id": "right-1", "entitlement_type": "owned",
            "state": "active", "loan_id": None, "expires_at": None,
        },
        "state": state, "origin": "user_declaration",
        "created_at": 1, "revoked_at": 2 if state == "revoked" else None,
    }
    storage.metadata_set(_PREFIX + relation, json.dumps(row, sort_keys=True))
    return row


def _run(tmp_path):
    service, artifact, placement = make_service(tmp_path)
    broker = LibraryMaintenanceBrokerImpl(service)
    old = tmp_path / "library" / "Book.epub"
    new = tmp_path / "library" / "Author - Book.epub"
    plan = broker.plan(LibraryMutationRequest("normalize_filename", placement_id=placement))
    return service, artifact, placement, broker, plan, old, new


def test_active_declared_link_blocks_file_rename_without_sqlite_or_hardlink_change(tmp_path):
    service, artifact, placement, broker, plan, old, new = _run(tmp_path)
    before = old.read_bytes()
    _declare(service.storage, artifact, old)
    try:
        with pytest.raises(ValueError, match="relocate_active_declaration_requires_relink"):
            broker.run(plan)
        assert old.read_bytes() == before and not new.exists()
        assert service.storage.placement_get(placement)["path"] == str(old)
        row, = service.storage.catalog_entries()
        assert row["metadata"]["path"] == str(old)
        stored = json.loads(service.storage.metadata_get(_PREFIX + "a" * 32))
        assert stored["file"]["path"] == str(old)
    finally:
        service.storage.close()


def test_corrupt_declaration_registry_fails_closed_before_mutating_paths(tmp_path):
    service, artifact, placement, broker, plan, old, new = _run(tmp_path)
    service.storage.metadata_set(_PREFIX + "b" * 32, "{PRIVATE invalid")
    try:
        with pytest.raises(ValueError, match="relocate_declared_context_unavailable"):
            broker.run(plan)
        assert old.exists() and not new.exists()
        assert service.storage.placement_get(placement)["path"] == str(old)
    finally:
        service.storage.close()


@pytest.mark.parametrize("state,filename", [
    ("revoked", "Book.epub"),
    ("active", "unrelated.epub"),
])
def test_revoked_or_unrelated_statement_does_not_block_normalize(
    tmp_path, state, filename,
):
    service, artifact, placement, broker, plan, old, new = _run(tmp_path)
    statement_path = old if filename == "Book.epub" else old.with_name(filename)
    _declare(service.storage, artifact, statement_path, state=state)
    try:
        result = broker.run(plan)
        assert result.ok and new.is_file() and not old.exists()
        assert service.storage.placement_get(placement)["path"] == str(new)
        statement = json.loads(service.storage.metadata_get(_PREFIX + "a" * 32))
        assert statement["file"]["path"] == str(statement_path)
    finally:
        service.storage.close()
