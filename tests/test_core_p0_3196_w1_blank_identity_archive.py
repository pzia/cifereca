"""P0 #3196 W1: a blank or padded Kobo identity is an archive, not A/B.

SQLite-only checks: no provider contact, file reads, downloads or migration.
The v0.7 source PK cannot supply a trustworthy profile from title/SHA/path.
"""
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
            name: db.execute("SELECT * FROM " + name + " ORDER BY 1").fetchall()
            for name in _TABLES
        }


def _archive(*, identity, status="complete", account=""):
    return {
        "provider_id": "kobo", "credential_id": "vault",
        "item_id": "edition", "revision_id": "historical-r1",
        "identity_id": identity, "account_id": account,
        "entitlement_type": "owned", "title": "Old Kobo backup",
        "status": status, "sha256": "a" * 64, "size": 42,
        "format": "epub", "local_path": "/offline/legacy.epub",
    }


@pytest.mark.parametrize("identity", ("", " ", "\t", " alice", "alice ", "\nalice"))
@pytest.mark.parametrize("status", ("pending", "complete", "reconcile_required"))
def test_invalid_profile_label_keeps_only_legacy_archive(tmp_path, identity, status):
    store = Storage(tmp_path / "state.sqlite3")
    try:
        before = _snapshot(store)
        for attempt in range(2):
            store.backup_put({
                **_archive(identity=identity, status=status),
                "title": "Old Kobo backup " + str(attempt),
            })
            row = store.backup_get("kobo", "vault", "edition")
            assert row is not None
            assert row["identity_id"] == identity
            assert row["account_id"] == ""
            assert row["title"] == "Old Kobo backup " + str(attempt)
            assert _snapshot(store) == before
    finally:
        store.close()


def test_exact_profile_still_accepts_kobo_primary_empty_account(tmp_path):
    """Empty account_id is Kobo's primary borrower, not a blank identity."""
    store = Storage(tmp_path / "state.sqlite3")
    try:
        store.backup_put(_archive(identity="alice", status="pending"))
        assert store.backup_get("kobo", "vault", "edition")["account_id"] == ""
        rights = store.entitlements(provider_id="kobo")
        assert len(rights) == 1
        assert rights[0]["account_id"] == ""
        assert rights[0]["identity_id"] == "alice"
    finally:
        store.close()


def test_invalid_profile_cannot_reassign_existing_canonical_owner(tmp_path):
    store = Storage(tmp_path / "state.sqlite3")
    try:
        store.entitlement_put({
            "provider_id": "kobo", "credential_id": "vault",
            "account_id": "", "identity_id": "bob", "item_id": "edition",
            "entitlement_type": "owned", "state": "active",
        })
        before = _snapshot(store)
        with pytest.raises(ValueError, match="backup_entitlement_owner_conflict"):
            store.backup_put(_archive(identity=""))
        assert store.backup_get("kobo", "vault", "edition") is None
        assert _snapshot(store) == before
    finally:
        store.close()
