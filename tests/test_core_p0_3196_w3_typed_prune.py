"""#3196 W3: a Kobo v0.7 manifest cannot erase typed-source history.

SQLite-only synthetic credentials/editions; no provider I/O, no rights
revocation and no physical deletion. A typed SourceReference retains its
historical backup projection until a separately attested archive cutover.
"""
from __future__ import annotations

import pytest

from cifereca.storage import Storage


def _backup(storage, item, *, credential="vault", provider="kobo", path=None):
    storage.backup_put({
        "provider_id": provider, "credential_id": credential,
        "identity_id": "alice", "account_id": "buyer",
        "item_id": item, "revision_id": "revision-1",
        "title": "Synthetic", "entitlement_type": "owned",
        "status": "complete", "local_path": str(path) if path else None,
    })


def _source(storage, *, credential="vault", edition="book", provider="kobo",
            identity="alice", account="buyer", state="active"):
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,"
            "state,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"typed:{provider}:{credential}:{edition}:{identity}:{account}",
             identity, provider, credential, account, "purchases", edition,
             "purchase", "purchase", state, 1, 1),
        )


def _canonical_state(storage):
    with storage._lock, storage._connect() as db:
        return tuple(
            tuple(db.execute("SELECT * FROM " + name + " ORDER BY 1").fetchall())
            for name in (
                "source_references", "origin_links", "entitlements",
                "acquisition_ledger", "artifacts", "placements",
            )
        )


@pytest.mark.parametrize("identity,account,state", [
    ("alice", "buyer", "active"),
    ("alice", "alternate-borrower", "active"),
    ("bob", "alternate-borrower", "unknown"),
    ("bob", "alternate-borrower", "inactive"),
])
def test_typed_source_protects_absent_legacy_projection_by_credential_edition(
    tmp_path, identity, account, state,
):
    storage = Storage(tmp_path / "legacy.sqlite3")
    try:
        original = tmp_path / "old.epub"
        original.write_bytes(b"unmodified-book")
        _backup(storage, "book", path=original)
        _backup(storage, "obsolete")
        _source(storage, identity=identity, account=account, state=state)
        historical = storage.backup_get("kobo", "vault", "book")
        canonical = _canonical_state(storage)
        result = storage.backup_prune_missing("kobo", "vault", set())
        assert result == {
            "pruned": 1, "local_preserved": 0,
            "entitlements_inactivated": 0,
        }
        assert storage.backup_get("kobo", "vault", "book") == historical
        assert storage.backup_get("kobo", "vault", "obsolete") is None
        assert _canonical_state(storage) == canonical
        assert original.read_bytes() == b"unmodified-book"
    finally:
        storage.close()


@pytest.mark.parametrize("reference", [
    {"provider": "numilog"},
    {"credential": "another-vault"},
    {"edition": "different-book"},
])
def test_unrelated_typed_source_does_not_block_exact_kobo_prune(
    tmp_path, reference,
):
    storage = Storage(tmp_path / "legacy.sqlite3")
    try:
        _backup(storage, "book")
        _source(storage, **reference)
        canonical = _canonical_state(storage)
        assert storage.backup_prune_missing("kobo", "vault", set())["pruned"] == 1
        assert storage.backup_get("kobo", "vault", "book") is None
        assert _canonical_state(storage) == canonical
    finally:
        storage.close()


def test_non_kobo_provider_preserves_existing_prune_semantics(tmp_path):
    storage = Storage(tmp_path / "legacy.sqlite3")
    try:
        _backup(storage, "book", provider="other")
        _source(storage, provider="other")
        before = _canonical_state(storage)
        assert storage.backup_prune_missing("other", "vault", set())["pruned"] == 1
        assert storage.backup_get("other", "vault", "book") is None
        assert _canonical_state(storage) == before
    finally:
        storage.close()
