"""#3196 W1: unbound Kobo v0.7 receipts must not mint canonical rights.

A missing profile or borrower is not permission to infer A/B from a 0.7
backup row. The scope-known legacy writer and recovery remain operational
pending their coordinated cutover, and the other providers are unchanged.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


_TABLES = (
    "entitlements", "acquisition_ledger", "artifacts", "placements",
    "catalog_entries", "source_references", "origin_links",
)


def _canonical(store):
    with store._lock, store._connect() as db:
        return tuple(
            tuple(db.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall())
            for table in _TABLES
        )


def _row(*, identity="alice", account=None, credential="vault", item="edition",
         status="pending", digest=None):
    return {
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": identity, "account_id": account,
        "item_id": item, "revision_id": "old-revision",
        "entitlement_type": "owned", "title": "Synthetic Kobo archive",
        "status": status, "sha256": digest,
        "size": 100 if digest else None,
    }


@pytest.mark.parametrize("identity,account", [(None, "borrower"), ("alice", None)])
@pytest.mark.parametrize("first,last", [
    ("pending", "complete"), ("acquiring", "error"),
    ("attention_required", "complete"), ("complete", "pending_acquisition"),
])
def test_unbound_live_backup_is_archive_not_canonical_acquisition(
    tmp_path, identity, account, first, last,
):
    store = Storage(tmp_path / "library.sqlite")
    try:
        baseline = _canonical(store)
        store.backup_put(_row(identity=identity, account=account, status=first))
        store.backup_put({
            **_row(identity=identity, account=account, status=last,
                   digest="a" * 64),
            "local_path": str(tmp_path / "offline.epub"),
            "attempt_count": 2,
        })
        archive = store.backup_get("kobo", "vault", "edition")
        assert archive["status"] == last
        assert archive["sha256"] == "a" * 64
        assert archive["attempt_count"] == 2
        assert _canonical(store) == baseline
        assert store.placements() == []
        assert store.acquisitions(provider_id="kobo") == []
        assert store.entitlements(provider_id="kobo") == []
    finally:
        store.close()


def test_partial_patch_reuses_existing_exact_borrower_and_mirror(tmp_path):
    store = Storage(tmp_path / "library.sqlite")
    try:
        store.backup_put(_row(account="buyer", status="pending"))
        assert store.acquisitions(provider_id="kobo") == []
        store.backup_put(_row(identity=None, account=None, status="complete"))
        archive = store.backup_get("kobo", "vault", "edition")
        assert archive["account_id"] == "buyer"
        assert archive["status"] == "complete"
        assert store.acquisitions(provider_id="kobo") == []
        assert store.entitlements(provider_id="kobo") == []
    finally:
        store.close()


def test_unbound_initial_row_can_be_bound_without_inventing_prior_right(tmp_path):
    store = Storage(tmp_path / "library.sqlite")
    try:
        store.backup_put(_row(identity=None, account=None))
        assert store.entitlements(provider_id="kobo") == []
        store.backup_put(_row(identity="alice", account="buyer", status="acquiring"))
        assert store.entitlements(provider_id="kobo") == []
        assert store.acquisitions(provider_id="kobo") == []
        assert store.backup_get("kobo", "vault", "edition")["account_id"] == "buyer"
    finally:
        store.close()


@pytest.mark.parametrize("credential,identity", [
    ("vault-a", "alice"), ("vault-b", "bob"),
])
def test_unbound_profiles_do_not_mint_shared_sha_placement(
    tmp_path, credential, identity,
):
    store = Storage(tmp_path / "library.sqlite")
    try:
        store.backup_put(_row(
            identity=identity, account=None, credential=credential,
            status="complete", digest="a" * 64,
        ))
        assert store.acquisitions(provider_id="kobo") == []
        assert store.placements() == []
        assert store.artifacts() == []
        assert store.backup_get("kobo", credential, "edition") is not None
    finally:
        store.close()


def test_unbound_legacy_row_respects_existing_canonical_owner(tmp_path):
    store = Storage(tmp_path / "library.sqlite")
    try:
        row = _row(account=None)
        store.entitlement_put({
            **row, "identity_id": "bob", "state": "active",
        })
        canonical = _canonical(store)
        with pytest.raises(ValueError, match="backup_entitlement_owner_conflict"):
            store.backup_put(row)
        assert store.backup_get("kobo", "vault", "edition") is None
        assert _canonical(store) == canonical
    finally:
        store.close()


def test_non_kobo_legacy_mirror_remains_active(tmp_path):
    store = Storage(tmp_path / "library.sqlite")
    try:
        store.backup_put({
            **_row(identity="alice", account=None, status="complete"),
            "provider_id": "other",
        })
        assert len(store.acquisitions(provider_id="other")) == 1
        assert len(store.entitlements(provider_id="other")) == 1
    finally:
        store.close()


def test_bad_legacy_path_is_rejected_before_archive_write(tmp_path):
    store = Storage(tmp_path / "library.sqlite")
    try:
        with pytest.raises(ValueError, match="backup_legacy_path_invalid"):
            store.backup_put({**_row(account=None), "local_path": "/books/\x00bad"})
        assert store.backup_list("kobo") == []
    finally:
        store.close()
