"""#3196 W1: live passive Kobo checkpoints are archives, not canonical A/B.

The v2->v3 importer already skipped never-attempted pending/queued rows.
The live writer must apply the same exact predicate, including after merge
of partial updates, without changing active W2/W4 legacy recovery.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


_CANONICAL = ("entitlements", "acquisition_ledger", "artifacts", "placements",
              "catalog_entries", "source_references", "origin_links")


def _snapshot(storage):
    with storage._lock, storage._connect() as db:
        return tuple(
            tuple(db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall())
            for table in _CANONICAL
        )


def _row(status="pending", **changed):
    return {
        "provider_id": "kobo", "credential_id": "vault",
        "identity_id": "alice", "account_id": "buyer",
        "entitlement_type": "owned", "item_id": "edition",
        "revision_id": "remote-revision", "title": "Archive",
        "status": status, **changed,
    }


@pytest.mark.parametrize("status", ("pending", "queued", "skipped_archived"))
def test_live_passive_receipt_and_replays_never_publish_core(tmp_path, status):
    storage = Storage(tmp_path / "store.sqlite")
    try:
        baseline = _snapshot(storage)
        for index in range(3):
            storage.backup_put(_row(status, title="Passive " + str(index)))
            assert storage.backup_get("kobo", "vault", "edition")["title"] == (
                "Passive " + str(index)
            )
            assert _snapshot(storage) == baseline
    finally:
        storage.close()


@pytest.mark.parametrize("clue", [
    {"attempt_count": 1}, {"last_attempt_at": 1234},
    {"local_path": "/old/delivery.epub"}, {"sha256": "a" * 64, "size": 42},
    {"attention_code": "missing_delegation_scope"}, {"error": "interrupted"},
    {"executor": "kobo-adept"}, {"acquisition_strategy": "legacy"},
    {"job_id": "old-job"}, {"adept_profile": "device"},
    {"loan_id": "loan-1"}, {"expires_at": 100},
])
def test_live_pending_with_recovery_clue_preserves_legacy_mirror(tmp_path, clue):
    storage = Storage(tmp_path / "store.sqlite")
    try:
        storage.backup_put(_row("pending", **clue))
        assert storage.backup_get("kobo", "vault", "edition") is not None
        assert len(storage.acquisitions(provider_id="kobo")) == 1
        assert len(storage.entitlements(provider_id="kobo")) == 1
    finally:
        storage.close()


def test_passive_replay_after_completed_legacy_receipt_preserves_core(tmp_path):
    storage = Storage(tmp_path / "store.sqlite")
    try:
        storage.backup_put(_row("complete", sha256="a" * 64, size=42))
        before = _snapshot(storage)
        storage.backup_put(_row("queued", sha256=None, size=None))
        # Historical fields can change for diagnostics, but an archive-only
        # observation must not update canonical rights/ledger/placements.
        assert storage.backup_get("kobo", "vault", "edition")["status"] == "queued"
        assert _snapshot(storage) == before
    finally:
        storage.close()


def test_passive_receipt_keeps_owner_conflict_veto_atomic(tmp_path):
    storage = Storage(tmp_path / "store.sqlite")
    try:
        storage.entitlement_put({
            **_row("pending"), "identity_id": "bob", "state": "active",
        })
        before = _snapshot(storage)
        with pytest.raises(ValueError, match="backup_entitlement_owner_conflict"):
            storage.backup_put(_row())
        assert storage.backup_get("kobo", "vault", "edition") is None
        assert _snapshot(storage) == before
    finally:
        storage.close()


def test_non_kobo_pending_mirror_unchanged(tmp_path):
    storage = Storage(tmp_path / "store.sqlite")
    try:
        storage.backup_put({**_row(), "provider_id": "external"})
        assert len(storage.acquisitions(provider_id="external")) == 1
        assert len(storage.entitlements(provider_id="external")) == 1
    finally:
        storage.close()
