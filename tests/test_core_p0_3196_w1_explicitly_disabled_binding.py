"""P0 #3196 W1: an explicitly disabled Kobo binding can only update archive.

The historical v0.7 PK has no borrower/branch.  A disabled source cannot
grant a canonical right or local presence, even with a plausible old SHA.
Tests use synthetic SQLite only; they do not attest provider rights.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


CANONICAL_TABLES = (
    "entitlements", "acquisition_ledger", "artifacts", "placements",
    "catalog_entries", "source_references", "origin_links",
)


def _canonical_snapshot(storage):
    with storage._lock, storage._connect() as db:
        return {
            name: db.execute(f"SELECT * FROM {name}").fetchall()
            for name in CANONICAL_TABLES
        }


def _legacy_row(*, status="complete", credential="vault", identity="alice"):
    return {
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": identity, "account_id": "account-a",
        "item_id": "edition", "revision_id": "historic-revision",
        "title": "Archived Kobo delivery", "status": status,
        "sha256": "a" * 64, "size": 42, "format": "epub",
        "local_path": "/nonexistent/synthetic-historical.epub",
    }


@pytest.mark.parametrize("status", (
    "complete", "pending", "pending_acquisition", "acquiring",
    "attention_required", "error", "reconcile_required", "reconcile_conflict",
))
def test_disabled_kobo_connection_never_mirrors_legacy_state(tmp_path, status):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "vault", "enabled": False,
        })
        before = _canonical_snapshot(storage)
        row = _legacy_row(status=status)
        for _ in range(2):
            storage.backup_put(row)
            archive = storage.backup_get("kobo", "vault", "edition")
            assert archive is not None
            assert archive["status"] == status
            assert _canonical_snapshot(storage) == before
    finally:
        storage.close()


def test_disabling_kobo_connection_stops_replay_from_rewriting_existing_core_rows(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        binding = {"identity_id": "alice", "provider_id": "kobo", "credential_id": "vault"}
        storage.provider_connection_put({**binding, "enabled": True})
        storage.backup_put(_legacy_row(status="pending"))
        assert storage.entitlements(provider_id="kobo")
        before = _canonical_snapshot(storage)
        storage.provider_connection_put({**binding, "enabled": False})
        storage.backup_put(_legacy_row(status="complete"))
        assert storage.backup_get("kobo", "vault", "edition")["status"] == "complete"
        assert _canonical_snapshot(storage) == before
    finally:
        storage.close()


def test_disabled_kobo_veto_is_exact_credential_and_does_not_block_other_sources(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "disabled", "enabled": False,
        })
        storage.provider_connection_put({
            "identity_id": "bob", "provider_id": "kobo",
            "credential_id": "enabled", "enabled": True,
        })
        storage.backup_put(_legacy_row(credential="disabled", identity="alice", status="pending"))
        assert storage.entitlements(provider_id="kobo") == []
        storage.backup_put(_legacy_row(credential="enabled", identity="bob", status="pending"))
        rights = storage.entitlements(provider_id="kobo")
        assert len(rights) == 1
        assert rights[0]["credential_id"] == "enabled"
    finally:
        storage.close()
