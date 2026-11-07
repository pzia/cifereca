"""P0 #3196/W1: a malformed legacy Kobo borrower is NOT a Core SourceKey.

SQLite-only, no supplier calls, filesystem reads or conversion of an archive.
The exact empty primary Kobo borrower remains valid.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


_TABLES = (
    "entitlements", "acquisition_ledger", "artifacts", "placements",
    "catalog_entries", "source_references", "origin_links",
)


def _snapshot(storage):
    with storage._lock, storage._connect() as db:
        return {
            name: db.execute("SELECT * FROM " + name + " ORDER BY 1").fetchall()
            for name in _TABLES
        }


def _old(account, *, status="complete", provider="kobo"):
    return {
        "provider_id": provider, "credential_id": "vault",
        "identity_id": "alice", "account_id": account,
        "item_id": "edition", "revision_id": "historic-r1",
        "entitlement_type": "owned", "title": "Legacy backup",
        "status": status, "sha256": "a" * 64, "size": 42,
        "format": "epub", "local_path": "/offline/legacy.epub",
    }


@pytest.mark.parametrize("account", (
    " ", "\t", " buyer", "buyer ", "\nbuyer", "buyer\n",
    "buyer\r", "buyer\x00suffix",
))
@pytest.mark.parametrize("status", (
    "pending", "acquiring", "complete", "reconcile_required",
))
def test_bad_borrower_retained_raw_but_never_mints_core_rows(
    tmp_path, account, status,
):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        before = _snapshot(storage)
        for attempt in range(2):
            storage.backup_put({
                **_old(account, status=status),
                "title": f"Legacy title {attempt}",
            })
            archive = storage.backup_get("kobo", "vault", "edition")
            assert archive is not None
            assert archive["account_id"] == account
            assert archive["status"] == status
            assert archive["title"] == f"Legacy title {attempt}"
            assert _snapshot(storage) == before
    finally:
        storage.close()


@pytest.mark.parametrize("account", ("", "buyer"))
def test_exact_primary_or_named_borrower_retains_legacy_compatibility(
    tmp_path, account,
):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.backup_put(_old(account, status="pending"))
        rights = storage.entitlements(provider_id="kobo")
        assert len(rights) == 1
        assert rights[0]["identity_id"] == "alice"
        assert rights[0]["account_id"] == account
        assert storage.backup_get("kobo", "vault", "edition")["account_id"] == account
    finally:
        storage.close()


def test_invalid_borrower_archive_still_vetoes_foreign_canonical_owner(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.entitlement_put({
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "bob", "account_id": " buyer",
            "item_id": "edition", "entitlement_type": "owned",
            "state": "active",
        })
        before = _snapshot(storage)
        with pytest.raises(ValueError, match="backup_entitlement_owner_conflict"):
            storage.backup_put(_old(" buyer"))
        assert storage.backup_get("kobo", "vault", "edition") is None
        assert _snapshot(storage) == before
    finally:
        storage.close()


def test_non_kobo_provider_keeps_historical_projection(tmp_path):
    storage = Storage(tmp_path / "state.sqlite3")
    try:
        storage.backup_put(_old(" borrower", provider="external"))
        assert storage.entitlements(provider_id="external")
        assert storage.backup_get("external", "vault", "edition") is not None
    finally:
        storage.close()
