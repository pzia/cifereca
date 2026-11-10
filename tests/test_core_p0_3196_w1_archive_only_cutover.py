"""#3196 W1: Kobo 0.7 storage writes are archive-only after #4858 cutover."""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


_TABLES = (
    "entitlements", "acquisition_ledger", "artifacts", "placements",
    "catalog_entries", "source_references", "origin_links",
)


def _snapshot(store):
    with store._lock, store._connect() as db:
        return {
            table: tuple(db.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall())
            for table in _TABLES
        }


def _archive(credential, owner, account, status, *, edition="book", sha=None):
    return {
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": owner, "account_id": account,
        "item_id": edition, "revision_id": "rev-old",
        "title": "Offline archive", "entitlement_type": "owned",
        "status": status, "local_path": "/nonexistent/old.epub",
        "size": 128 if sha else None, "sha256": sha,
    }


@pytest.mark.parametrize("first,second", [
    ("pending", "complete"), ("acquiring", "error"),
    ("complete", "reconcile_required"), ("ok", "pending_acquisition"),
    ("reconcile_conflict", "complete"),
])
def test_first_and_replayed_w1_kobo_rows_never_mint_a_or_b(tmp_path, first, second):
    store = Storage(tmp_path / "state.db")
    try:
        initial = _snapshot(store)
        for credential, identity, borrower in [
            ("credential-a", "alice", "buyer-a"),
            ("credential-b", "bob", "buyer-b"),
        ]:
            row = _archive(credential, identity, borrower, first, sha="a" * 64)
            store.backup_put(row)
            store.backup_put({**row, "status": second, "sha256": "b" * 64})
            stored = store.backup_get("kobo", credential, "book")
            assert stored["status"] == second
            assert stored["sha256"] == "b" * 64
        assert _snapshot(store) == initial
        assert len(store.backup_list("kobo")) == 2
    finally:
        store.close()


def test_foreign_borrower_rebind_aborts_atomically_without_positive_right(tmp_path):
    store = Storage(tmp_path / "state.db")
    try:
        row = _archive("vault", "alice", "account-a", "pending")
        store.backup_put(row)
        archive = store.backup_get("kobo", "vault", "book")
        before = _snapshot(store)
        with pytest.raises(ValueError, match="backup_projection_owner_conflict"):
            store.backup_put({**row, "account_id": "account-b", "status": "complete"})
        assert store.backup_get("kobo", "vault", "book") == archive
        assert _snapshot(store) == before
    finally:
        store.close()


def test_non_kobo_legacy_provider_keeps_existing_compatibility_behavior(tmp_path):
    store = Storage(tmp_path / "state.db")
    try:
        row = _archive("card", "alice", "buyer", "pending")
        store.backup_put({**row, "provider_id": "other"})
        assert len(store.entitlements(provider_id="other")) == 1
        assert len(store.acquisitions(provider_id="other")) == 1
        assert store.backup_get("other", "card", "book") is not None
    finally:
        store.close()
