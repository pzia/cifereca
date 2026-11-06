"""#3196 W1: an active Kobo 0.7 backup is history, not canonical authority.

Offline SQLite contract. Legacy recovery remains readable/writable, but no
backup status or declared SHA can generate current B rights, provenance,
acquisition receipts or physical Placements. No provider HTTP or files.
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
        return tuple(
            tuple(db.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall())
            for table in _TABLES
        )


def _legacy(*, status="pending", item="edition", credential="vault",
            identity="alice", account="buyer", digest=None):
    return {
        "provider_id": "kobo", "credential_id": credential,
        "identity_id": identity, "account_id": account,
        "item_id": item, "revision_id": "old-revision",
        "title": "Synthetic historical Kobo item",
        "entitlement_type": "owned", "status": status,
        "sha256": digest, "size": 100 if digest else None,
    }


@pytest.mark.parametrize("first,last", [
    ("pending", "complete"), ("acquiring", "error"),
    ("attention_required", "complete"), ("complete", "pending_acquisition"),
])
def test_live_legacy_kobo_updates_never_project_canonical_rights_or_receipts(
    tmp_path, first, last,
):
    store = Storage(tmp_path / "library.sqlite")
    try:
        canonical = _snapshot(store)
        store.backup_put(_legacy(status=first))
        store.backup_put({
            **_legacy(status=last, digest="a" * 64),
            "local_path": str(tmp_path / "disconnected.epub"),
            "attempt_count": 2,
        })
        legacy = store.backup_get("kobo", "vault", "edition")
        assert legacy is not None and legacy["status"] == last
        assert legacy["sha256"] == "a" * 64
        assert legacy["attempt_count"] == 2
        assert _snapshot(store) == canonical
        assert store.artifacts() == []
        assert store.placements() == []
        assert store.acquisitions(provider_id="kobo") == []
        assert store.entitlements(provider_id="kobo") == []
    finally:
        store.close()


def test_live_legacy_writer_cannot_downgrade_independent_canonical_state(tmp_path):
    store = Storage(tmp_path / "library.sqlite")
    try:
        source = _legacy()
        store.entitlement_put({
            **source, "state": "inactive", "revision_id": "provider-revision",
            "metadata": {"source": "attested_provider"},
        })
        store.acquisition_put({
            "provider_id": "kobo", "credential_id": "vault",
            "identity_id": "alice", "account_id": "buyer",
            "item_id": "edition", "status": "complete",
            "metadata": {"schema": "canonical.test-receipt"},
        })
        before = _snapshot(store)
        for state in ("pending", "acquiring", "complete", "error"):
            store.backup_put(_legacy(status=state, digest="b" * 64))
            assert _snapshot(store) == before
        assert store.backup_get("kobo", "vault", "edition")["status"] == "error"
    finally:
        store.close()


def test_two_legacy_profiles_same_sha_do_not_create_shared_canonical_placement(
    tmp_path,
):
    store = Storage(tmp_path / "library.sqlite")
    try:
        before = _snapshot(store)
        for credential, identity, account in (
            ("vault-a", "alice", "account-a"),
            ("vault-b", "bob", "account-b"),
        ):
            store.backup_put(_legacy(
                item="edition", credential=credential,
                identity=identity, account=account, status="complete",
                digest="c" * 64,
            ))
        assert len(store.backup_list("kobo")) == 2
        assert _snapshot(store) == before
    finally:
        store.close()


def test_legacy_owner_rebinding_is_still_rejected_before_any_canonical_change(
    tmp_path,
):
    store = Storage(tmp_path / "library.sqlite")
    try:
        store.backup_put(_legacy())
        before = _snapshot(store)
        history = store.backup_get("kobo", "vault", "edition")
        with pytest.raises(ValueError, match="backup_projection_owner_conflict"):
            store.backup_put(_legacy(account="other-borrower"))
        assert store.backup_get("kobo", "vault", "edition") == history
        assert _snapshot(store) == before
    finally:
        store.close()


def test_non_kobo_legacy_mirror_is_unmodified(tmp_path):
    store = Storage(tmp_path / "library.sqlite")
    try:
        row = {**_legacy(status="complete"), "provider_id": "other"}
        store.backup_put(row)
        assert len(store.acquisitions(provider_id="other")) == 1
        assert len(store.entitlements(provider_id="other")) == 1
        assert store.backup_get("other", "vault", "edition") is not None
    finally:
        store.close()
