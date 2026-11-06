"""P0 #3196 W3 retirement: no Kobo backup deletion on manifest absence.

Offline SQLite/service contract; no provider call, file transfer or DRM.
Kobo's v0.7 projection is an archive, not a negative manifest cache.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from cifereca.storage import Storage
from tests.test_kobo_backup_reconcile_prune import _service


@pytest.mark.parametrize("state", [
    "pending", "complete", "acquiring", "reconcile_required",
    "skipped_archived", "attention_required", "error",
])
def test_kobo_prune_is_readonly_even_for_unqualified_empty_records(
    tmp_path, state,
):
    store = Storage(tmp_path / "legacy.sqlite3")
    try:
        for item_id, status in (
            ("missing-from-manifest", state),
            ("still-visible", "pending"),
        ):
            store.backup_put({
                "provider_id": "kobo", "credential_id": "vault",
                "identity_id": "profile", "account_id": "borrower",
                "item_id": item_id, "revision_id": "old-revision",
                "title": "Synthetic", "status": status,
                "local_path": None, "sha256": None, "size": None,
            })
        before = store.backup_list("kobo", "vault")
        rights = store.entitlements()
        receipt = store.acquisitions()

        # The retired API must not even open SQLite; no BEGIN IMMEDIATE,
        # SELECT, DELETE or covert rights/ledger mutation is permitted.
        with patch.object(Storage, "_connect", side_effect=AssertionError(
            "Kobo manifest prune attempted SQLite I/O"
        )):
            for seen in (set(), {"still-visible"}, ["still-visible"]):
                assert store.backup_prune_missing(
                    "kobo", "vault", seen,
                ) == {
                    "pruned": 0, "local_preserved": 0,
                    "entitlements_inactivated": 0,
                }
            for invalid in (None, "", {"": "row"}, [""], [123], ["\x00"]):
                with pytest.raises(ValueError, match="invalid_authoritative_manifest_item_ids"):
                    store.backup_prune_missing("kobo", "vault", invalid)

        assert store.backup_list("kobo", "vault") == before
        assert store.entitlements() == rights
        assert store.acquisitions() == receipt
    finally:
        store.close()


def test_complete_kobo_manifest_does_not_call_retired_pruner(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path, manifest=[])
    try:
        service.storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault-1",
            "identity_id": "default", "account_id": "borrower",
            "item_id": "unseen", "title": "Unseen old edition",
            "revision_id": "old-revision", "status": "pending",
        })
        before = service.storage.backup_get("kobo", "vault-1", "unseen")
        monkeypatch.setattr(
            service.storage, "backup_prune_missing",
            lambda *_args, **_kwargs: pytest.fail(
                "Kobo synchronization must not invoke W3 pruner"
            ),
        )
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_args, **_kwargs: pytest.fail("no provider GET permitted"),
        )
        for _ in range(2):
            summary = service.sync_kobo_backup_account("vault-1")
            assert summary["legacy_pruned"] == 0
            assert summary["legacy_entitlements_inactivated"] == 0
            assert service.storage.backup_get("kobo", "vault-1", "unseen") == before
    finally:
        service.close()
