"""#3196/W3 — an absent remote manifest cannot erase interrupted Kobo work.

All data are isolated SQLite fixtures; no Kobo HTTP, ADEPT or physical books.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage
from tests.test_kobo_backup_reconcile_prune import _service


def _record(item_id, *, status="acquiring", provider="kobo", credential="vault"):
    return {
        "provider_id": provider, "credential_id": credential,
        "identity_id": "alice", "account_id": "account-a",
        "item_id": item_id, "revision_id": "revision-a",
        "title": "Synthetic interrupted book",
        "entitlement_type": "owned", "status": status,
        "local_path": None, "sha256": None,
        "acquisition_strategy": "exact-synthetic",
        "executor": "legacy", "job_id": "synthetic-interrupted-job",
        "attempt_count": 2,
    }


@pytest.mark.parametrize("state", ["acquiring", "pending_acquisition"])
def test_interrupted_no_path_kobo_row_is_not_pruned_on_empty_manifest(
    tmp_path, state,
):
    store = Storage(tmp_path / "db.sqlite3")
    try:
        store.backup_put(_record("interrupted", status=state))
        store.backup_put(_record("stale-pending", status="pending"))
        store.backup_put(_record("other-credential", credential="other"))
        store.backup_put(_record("other-provider", provider="external"))
        original = store.backup_get("kobo", "vault", "interrupted")
        other_credential = store.backup_get("kobo", "other", "other-credential")
        rights = store.entitlements()
        ledger = store.acquisitions()
        for expected_pruned in (0, 0):
            assert store.backup_prune_missing("kobo", "vault", set()) == {
                "pruned": expected_pruned,
                "local_preserved": 0, "entitlements_inactivated": 0,
            }
            assert store.backup_get("kobo", "vault", "interrupted") == original
            assert store.backup_get("kobo", "vault", "stale-pending") is not None
            assert store.backup_get("kobo", "other", "other-credential") == other_credential
            assert store.backup_get("external", "vault", "other-provider") is not None
            assert store.entitlements() == rights
            assert store.acquisitions() == ledger
        # The new fence is Kobo-specific, not a general policy for every
        # legacy projection or provider's complete manifest.
        assert store.backup_prune_missing("external", "vault", set())["pruned"] == 1
        assert store.backup_get("external", "vault", "other-provider") is None
    finally:
        store.close()


@pytest.mark.parametrize("state", ["acquiring", "pending_acquisition"])
def test_seen_interrupted_kobo_record_is_not_modified_by_prune(
    tmp_path, state,
):
    store = Storage(tmp_path / "db.sqlite3")
    try:
        store.backup_put(_record("still-listed", status=state))
        before = store.backup_get("kobo", "vault", "still-listed")
        assert store.backup_prune_missing(
            "kobo", "vault", {"still-listed"},
        )["pruned"] == 0
        assert store.backup_get("kobo", "vault", "still-listed") == before
    finally:
        store.close()


def test_empty_manifest_recovery_is_durable_and_makes_no_download(tmp_path, monkeypatch):
    service = _service(tmp_path, manifest=[])
    try:
        service.storage.backup_put({
            **_record("interrupted", status="acquiring", credential="vault-1"),
            "identity_id": "default",
        })
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_args, **_kwargs: pytest.fail(
                "an absent manifest does not attest a fresh download offer"
            ),
        )
        first = service.sync_kobo_backup()
        receipt = service.storage.backup_get("kobo", "vault-1", "interrupted")
        assert first["downloaded"] == 0
        assert first["recovered_interrupted"] == 1
        assert first["legacy_pruned"] == 0
        assert receipt is not None
        assert receipt["status"] == "pending_acquisition"
        assert receipt["revision_id"] == "revision-a"
        assert receipt["attempt_count"] == 2
        assert receipt["local_path"] is None
        second = service.sync_kobo_backup()
        assert second["downloaded"] == 0
        assert second["recovered_interrupted"] == 0
        assert second["legacy_pruned"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "interrupted") == receipt
    finally:
        service.close()


@pytest.mark.parametrize("ledger_state", [
    "acquiring", "downloading", "pending_acquisition",
    "reconcile_required", "reconcile_conflict",
])
@pytest.mark.parametrize("legacy_state", ["pending", "complete"])
def test_stale_kobo_projection_keeps_exact_interrupted_ledger_pointer(
        tmp_path, ledger_state, legacy_state):
    """The SQLite ledger, not a stale backup status, owns crash recovery."""
    store = Storage(tmp_path / "db.sqlite3")
    try:
        store.backup_put(_record("ledger-interrupted", status=legacy_state))
        store.backup_put(_record("ordinary-stale", status="pending"))
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET status=? WHERE "
                "provider_id='kobo' AND credential_id='vault' "
                "AND item_id='ledger-interrupted'",
                (ledger_state,),
            )
        before = store.backup_get("kobo", "vault", "ledger-interrupted")
        rights = store.entitlements()
        ledger = store.acquisitions()
        assert store.backup_prune_missing("kobo", "vault", set()) == {
            "pruned": 0, "local_preserved": 0,
            "entitlements_inactivated": 0,
        }
        assert store.backup_get("kobo", "vault", "ledger-interrupted") == before
        assert store.backup_get("kobo", "vault", "ordinary-stale") is not None
        assert store.entitlements() == rights
        assert store.acquisitions() == ledger
        # Even after the ledger resolves, a manifest cannot erase an
        # unqualified historical row without a separate migration.
        with store._lock, store._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET status='complete' WHERE "
                "provider_id='kobo' AND credential_id='vault' "
                "AND item_id='ledger-interrupted'",
            )
        assert store.backup_prune_missing("kobo", "vault", set())["pruned"] == 0
        assert store.backup_get("kobo", "vault", "ledger-interrupted") == before
    finally:
        store.close()
