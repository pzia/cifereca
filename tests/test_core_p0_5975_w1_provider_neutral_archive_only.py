"""#5975/#3196: public W1 checkpoints from ANY provider are read-only Core archives.

No source attestation or verified byte evidence can be inferred from the
legacy provider/credential/edition PK. The historical v2->v3 import is distinct.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from cifereca.storage import Storage


_PROVIDERS = ("kobo", "paris", "numilog", "unregistered-seller")
_CANONICAL = (
    "entitlements", "acquisition_ledger", "artifacts", "placements",
    "catalog_entries", "source_references", "origin_links",
)


def _snapshot(store):
    with store._lock, store._connect() as db:
        return {
            table: tuple(db.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall())
            for table in _CANONICAL
        }


def _row(provider, *, status="complete", title="Old book", digest="a" * 64,
         identity="alice", account="buyer"):
    return {
        "provider_id": provider, "credential_id": "account",
        "item_id": "edition", "identity_id": identity, "account_id": account,
        "entitlement_type": "owned", "revision_id": "legacy-revision",
        "title": title, "status": status, "sha256": digest, "size": 123,
        "local_path": "/unverified/legacy-book.epub",
        "updated_at": 1000,
    }


@pytest.mark.parametrize("provider", _PROVIDERS)
@pytest.mark.parametrize("status", ("pending", "acquiring", "complete", "error"))
def test_w1_new_archive_never_mints_any_core_state(tmp_path, provider, status):
    store = Storage(tmp_path / "state.sqlite")
    try:
        before = _snapshot(store)
        with store._lock, store._connect() as db:
            for table in _CANONICAL:
                db.execute(
                    f"CREATE TRIGGER block_w1_{table} BEFORE INSERT ON {table} "
                    "BEGIN SELECT RAISE(ABORT,'w1_minted_canonical'); END"
                )
        store.backup_put(_row(provider, status=status))
        assert store.backup_get(provider, "account", "edition")["status"] == status
        assert _snapshot(store) == before
        # Metadata and the old archive can refresh; even an alleged B receipt
        # or repeated complete checkpoint cannot create a canonical row.
        store.backup_put(_row(
            provider, status="complete", title="Updated metadata", digest="b" * 64,
        ))
        assert store.backup_get(provider, "account", "edition")["title"] == "Updated metadata"
        assert _snapshot(store) == before
    finally:
        store.close()


@pytest.mark.parametrize("provider", _PROVIDERS)
def test_w1_replay_cannot_change_independently_recorded_right(tmp_path, provider):
    store = Storage(tmp_path / "state.sqlite")
    try:
        store.entitlement_put({
            "provider_id": provider, "credential_id": "account",
            "account_id": "buyer", "item_id": "edition",
            "identity_id": "alice", "entitlement_type": "owned",
            "state": "inactive", "revision_id": "typed-only",
        })
        original = _snapshot(store)
        store.backup_put(_row(provider))
        store.backup_put(_row(provider, status="acquiring", digest="b" * 64))
        assert _snapshot(store) == original
        assert store.backup_get(provider, "account", "edition")["status"] == "acquiring"
        # The archive's own exact owner lock fires before the subsequent
        # independent-right collision check; both are fail-closed.
        with pytest.raises(ValueError, match="backup_(projection|entitlement)_owner_conflict"):
            store.backup_put(_row(provider, identity="bob"))
        assert _snapshot(store) == original
        assert store.backup_get(provider, "account", "edition")["identity_id"] == "alice"
    finally:
        store.close()


def test_w1_source_only_archive_cutover_is_structurally_enforced():
    source = inspect.getsource(Storage.backup_put)
    tree = ast.parse(source if source.startswith("def ") else __import__("textwrap").dedent(source))
    mirrors = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_mirror_backup_row"
    ]
    assert len(mirrors) == 1
    assert any(
        kw.arg == "archive_only"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is True
        for kw in mirrors[0].keywords
    )
    assert "_check_kobo_legacy_canonical_owner" not in source
    assert "_check_legacy_canonical_owner" in source
