"""#3196 W1: archived/reconciliation-only Kobo receipts are never Core writers.

Synthetic SQLite fixtures. The legacy 0.7 row remains available for
explicit review; neither its status nor its SHA is provider-attested evidence.
"""
from __future__ import annotations

import hashlib

import pytest

from cifereca.storage import Storage


@pytest.mark.parametrize("status", ("skipped_archived",))
def test_passive_kobo_checkpoint_does_not_mint_canonical_state(tmp_path, status):
    storage = Storage(tmp_path / "state.sqlite3")
    data = b"synthetic historical content"
    row = {
        "provider_id": "kobo", "credential_id": "vault",
        "identity_id": "alice", "account_id": "account-a",
        "item_id": "edition", "revision_id": "old-revision",
        "title": "Archived", "status": status, "format": "epub",
        "local_path": str(tmp_path / "historical.epub"),
        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
    }
    try:
        with storage._lock, storage._connect() as db:
            tables = ("entitlements", "acquisition_ledger", "artifacts",
                      "placements", "catalog_entries", "source_references",
                      "origin_links")
            before = {
                table: db.execute(f"SELECT * FROM {table}").fetchall()
                for table in tables
            }
        for _ in range(2):
            storage.backup_put(row)
            assert storage.backup_get("kobo", "vault", "edition")["status"] == status
            with storage._lock, storage._connect() as db:
                for table in tables:
                    assert db.execute(f"SELECT * FROM {table}").fetchall() == before[table]
    finally:
        storage.close()


@pytest.mark.parametrize("status", ("skipped_archived",))
def test_passive_kobo_checkpoint_cannot_rebind_existing_canonical_owner(
    tmp_path, status,
):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        right = {
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "entitlement_type": "owned",
            "revision_id": "provider-current", "state": "active",
        }
        storage.entitlement_put(right)
        before = storage.entitlements()
        row = {
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "bob", "account_id": "account-a",
            "item_id": "edition", "entitlement_type": "owned",
            "revision_id": "old", "title": "Old",
            "status": status,
        }
        with pytest.raises(ValueError, match="backup_entitlement_owner_conflict"):
            storage.backup_put(row)
        assert storage.backup_get("kobo", "vault", "edition") is None
        assert storage.entitlements() == before
    finally:
        storage.close()


@pytest.mark.parametrize("status", ("reconcile_required", "reconcile_conflict"))
def test_disconnected_reconciliation_with_complete_sha_is_archive_only(
    tmp_path, status,
):
    """W4 is retired: bytes in a v0.7 row cannot mint an offline right."""
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        row = {
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "title": "Review",
            "status": status, "sha256": "a" * 64, "size": 42,
            "local_path": str(tmp_path / "not-proof.epub"),
        }
        for _ in range(2):
            storage.backup_put(row)
        assert storage.backup_get("kobo", "vault", "edition")["status"] == status
        assert storage.entitlements(provider_id="kobo") == []
        assert storage.acquisitions(provider_id="kobo") == []
        assert storage.artifacts() == []
        assert storage.placements() == []
    finally:
        storage.close()


def test_other_provider_retains_historical_projection(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.backup_put({
            "provider_id": "external", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "title": "External",
            "status": "reconcile_required",
        })
        assert storage.backup_get("external", "vault", "edition") is not None
        assert storage.entitlements(provider_id="external")
    finally:
        storage.close()



@pytest.mark.parametrize("sha,size", [
    (None, None), ("a" * 64, None), (None, 12),
    ("bad-sha", 12), ("a" * 63, 12), ("a" * 64, 0),
    ("z" * 64, 12),
])
@pytest.mark.parametrize("status", ("reconcile_required", "reconcile_conflict"))
def test_unprovable_kobo_reconciliation_never_creates_positive_canonical_state(
    tmp_path, sha, size, status,
):
    """W4 cannot confirm missing SHA or size; archive the checkpoint only."""
    storage = Storage(tmp_path / "state.sqlite3")
    row = {
        "provider_id": "kobo", "credential_id": "vault",
        "identity_id": "alice", "account_id": "account-a",
        "item_id": "edition", "revision_id": "revision-a",
        "title": "Unresolved local candidates", "status": status,
        "sha256": sha, "size": size,
        "local_path": str(tmp_path / "ambiguous.epub"),
    }
    try:
        with storage._lock, storage._connect() as db:
            tables = (
                "entitlements", "acquisition_ledger", "artifacts",
                "placements", "catalog_entries", "source_references", "origin_links",
            )
            before = {
                table: db.execute("SELECT * FROM " + table).fetchall()
                for table in tables
            }
        for _ in range(2):
            storage.backup_put(row)
            assert storage.backup_get("kobo", "vault", "edition")["status"] == status
            with storage._lock, storage._connect() as db:
                assert all(
                    db.execute("SELECT * FROM " + table).fetchall() == before[table]
                    for table in tables
                )
    finally:
        storage.close()


def test_unprovable_conflict_retains_owner_collision_veto(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.entitlement_put({
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "entitlement_type": "owned",
            "state": "active",
        })
        with pytest.raises(ValueError, match="backup_entitlement_owner_conflict"):
            storage.backup_put({
                "provider_id": "kobo", "credential_id": "vault",
                "identity_id": "bob", "account_id": "account-a",
                "item_id": "edition", "entitlement_type": "owned",
                "title": "Unproven", "status": "reconcile_conflict",
            })
        assert storage.backup_get("kobo", "vault", "edition") is None
    finally:
        storage.close()


def test_unprovable_required_patch_preserves_previous_typed_canonical_evidence(tmp_path):
    """A 0.7 error must not downgrade independent Core rows on a retry."""
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        right = storage.entitlement_put({
            "provider_id": "kobo", "credential_id": "vault",
            "account_id": "account-a", "identity_id": "alice",
            "item_id": "edition", "entitlement_type": "owned",
            "revision_id": "provider-v2", "state": "active",
        })
        storage.acquisition_put({
            "acquisition_id": "independent-123",
            "entitlement_id": right, "provider_id": "kobo",
            "credential_id": "vault", "account_id": "account-a",
            "identity_id": "alice", "item_id": "edition",
            "status": "complete", "metadata": {"source": "provider-v2"},
        })
        before = storage.entitlements(), storage.acquisitions()
        row = {
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "revision_id": "historical-v1",
            "status": "reconcile_required", "sha256": None,
            "size": None, "local_path": str(tmp_path / "lost.epub"),
        }
        storage.backup_put(row)
        storage.backup_put({**row, "title": "Retried unproven"})
        assert storage.entitlements() == before[0]
        assert storage.acquisitions() == before[1]
        assert storage.backup_get("kobo", "vault", "edition")["title"] == "Retried unproven"
    finally:
        storage.close()


@pytest.mark.parametrize("status", ("reconcile_required", "reconcile_conflict"))
@pytest.mark.parametrize("connected_owner,mirrored", [
    (None, False), ("alice", True), ("bob", False),
])
def test_unprovable_reconciliation_retains_active_source_discovery_only_for_owner(
    tmp_path, connected_owner, mirrored, status,
):
    """Do not suppress legitimate Kobo OWN-manifest discoveries before W2."""
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        if connected_owner is not None:
            storage.provider_connection_put({
                "identity_id": connected_owner, "provider_id": "kobo",
                "credential_id": "vault",
            })
        storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "revision_id": "r1",
            "status": status,
            "title": "Local candidate without verified bytes",
            "sha256": None, "size": None,
        })
        assert (len(storage.acquisitions(provider_id="kobo")) == 1) is mirrored
        assert (len(storage.entitlements(provider_id="kobo")) == 1) is mirrored
        assert storage.artifacts() == []
        assert storage.backup_get("kobo", "vault", "edition") is not None
    finally:
        storage.close()



@pytest.mark.parametrize("status", ("reconcile_required", "reconcile_conflict"))
@pytest.mark.parametrize("enabled", (False, True))
def test_unproven_reconciliation_mirrors_only_enabled_exact_kobo_binding(
    tmp_path, status, enabled,
):
    """A disabled connection is not live source discovery authority (W1)."""
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "vault", "enabled": enabled,
        })
        row = {
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "revision_id": "legacy",
            "title": "No independently verified bytes",
            "status": status, "sha256": None, "size": None,
        }
        for _ in range(2):
            storage.backup_put(row)
        assert storage.backup_get("kobo", "vault", "edition")["status"] == status
        assert bool(storage.acquisitions(provider_id="kobo")) is enabled
        assert bool(storage.entitlements(provider_id="kobo")) is enabled
        assert storage.artifacts() == []
    finally:
        storage.close()


@pytest.mark.parametrize("status", ("reconcile_required", "reconcile_conflict"))
def test_disabling_connection_prevents_new_unproven_legacy_projection(
    tmp_path, status,
):
    """Do not delete old Core rows, but never mint a new one after disable."""
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "vault", "enabled": False,
        })
        storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "account-a",
            "item_id": "edition", "revision_id": "legacy",
            "status": status, "title": "Offline checkpoint",
            "sha256": "invalid", "size": 7,
        })
        assert storage.backup_get("kobo", "vault", "edition") is not None
        assert storage.acquisitions(provider_id="kobo") == []
        assert storage.entitlements(provider_id="kobo") == []
    finally:
        storage.close()


@pytest.mark.parametrize("status", ("reconcile_required", "reconcile_conflict"))
@pytest.mark.parametrize("connected_owner,expected_mirror", [
    (None, False), ("alice", True), ("bob", False),
])
def test_legacy_reconcile_sha_never_bypasses_active_exact_source_binding(
    tmp_path, status, connected_owner, expected_mirror,
):
    """Valid SHA/size cannot stand in for an enabled borrower/owner binding."""
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        if connected_owner:
            storage.provider_connection_put({
                "identity_id": connected_owner,
                "provider_id": "kobo", "credential_id": "vault",
                "enabled": True,
            })
        row = {
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "vault", "account_id": "primary",
            "item_id": "edition", "revision_id": "old-revision",
            "status": status, "sha256": "a" * 64, "size": 42,
            "local_path": str(tmp_path / "unverified.epub"),
        }
        for i in range(2):
            storage.backup_put({**row, "title": f"Old title {i}"})
        assert storage.backup_get("kobo", "vault", "edition")["title"] == "Old title 1"
        assert bool(storage.entitlements(provider_id="kobo")) is expected_mirror
        assert bool(storage.acquisitions(provider_id="kobo")) is expected_mirror
        assert bool(storage.placements()) is expected_mirror
        if expected_mirror:
            # W2 compatibility: only a legacy placeholder, never B presence.
            assert storage.placements()[0]["state"] == "unverified"
        else:
            assert storage.artifacts() == []
            assert storage.catalog_entries() == []
    finally:
        storage.close()


@pytest.mark.parametrize("status", ("reconcile_required", "reconcile_conflict"))
def test_disabled_exact_kobo_source_with_sha_is_archive_only(tmp_path, status):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "vault", "enabled": False,
        })
        storage.backup_put({
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "vault", "account_id": "primary",
            "item_id": "edition", "revision_id": "old-revision",
            "status": status, "sha256": "a" * 64, "size": 42,
            "local_path": str(tmp_path / "unverified.epub"),
        })
        assert storage.backup_get("kobo", "vault", "edition") is not None
        assert storage.entitlements(provider_id="kobo") == []
        assert storage.acquisitions(provider_id="kobo") == []
        assert storage.placements() == []
    finally:
        storage.close()


@pytest.mark.parametrize("status,attention_code", [
    ("attention_required", "archived_book"),
    ("attention_required", "missing_delegation_scope"),
    ("pending_acquisition", "interrupted_acquisition_review"),
    ("complete", "archived_book"),
])
def test_enabled_kobo_denial_and_interruption_codes_remain_archive_only(
    tmp_path, status, attention_code,
):
    """W1: a live credential is not consent, supplier right or B evidence."""
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "vault", "enabled": True,
        })
        tables = (
            "entitlements", "acquisition_ledger", "artifacts",
            "placements", "catalog_entries", "source_references",
            "origin_links",
        )
        with storage._lock, storage._connect() as db:
            before = {
                table: db.execute("SELECT * FROM " + table).fetchall()
                for table in tables
            }
        row = {
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "borrower",
            "item_id": "edition", "revision_id": "r1",
            "title": "Unqualified historical checkpoint",
            "status": status, "attention_code": attention_code,
            "sha256": "a" * 64, "size": 42,
            "local_path": str(tmp_path / "not-a-provider-delivery.epub"),
        }
        for _ in range(2):
            storage.backup_put(row)
        stored = storage.backup_get("kobo", "vault", "edition")
        assert stored["status"] == status
        assert stored["attention_code"] == attention_code
        assert stored["sha256"] == "a" * 64
        with storage._lock, storage._connect() as db:
            assert all(
                db.execute("SELECT * FROM " + table).fetchall() == before[table]
                for table in tables
            )
    finally:
        storage.close()


def test_denial_checkpoint_still_rejects_foreign_canonical_owner(tmp_path):
    """The archive-only cut must preserve atomic owner-collision checks."""
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.provider_connection_put({
            "identity_id": "alice", "provider_id": "kobo",
            "credential_id": "vault", "enabled": True,
        })
        storage.entitlement_put({
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "bob", "account_id": "borrower",
            "item_id": "edition", "entitlement_type": "owned",
            "state": "active",
        })
        with pytest.raises(ValueError, match="backup_entitlement_owner_conflict"):
            storage.backup_put({
                "provider_id": "kobo", "credential_id": "vault",
                "identity_id": "alice", "account_id": "borrower",
                "item_id": "edition", "entitlement_type": "owned",
                "status": "attention_required",
                "attention_code": "missing_delegation_scope",
            })
        assert storage.backup_get("kobo", "vault", "edition") is None
    finally:
        storage.close()
