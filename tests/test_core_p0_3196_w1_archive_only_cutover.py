"""#3196/#5975 W1: all live 0.7 checkpoints are now archive-only."""
from __future__ import annotations

import ast
import hashlib
import json
import inspect
import textwrap

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


def test_non_kobo_legacy_provider_is_archive_only_too(tmp_path):
    store = Storage(tmp_path / "state.db")
    try:
        row = _archive("card", "alice", "buyer", "pending")
        before = _snapshot(store)
        store.backup_put({**row, "provider_id": "other"})
        assert store.entitlements(provider_id="other") == []
        assert store.acquisitions(provider_id="other") == []
        assert store.backup_get("other", "card", "book") is not None
        assert _snapshot(store) == before
    finally:
        store.close()


def test_w1_generic_mirror_contains_no_supplier_literal():
    """The inactive typed Kobo veto must not return to the publication path.

    Kobo v2 archives skip the v3 importer. The public backup_put always
    passes archive_only=True for every provider; the historical v3 importer
    is a separate retained migration writer, not a live W1 source.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(Storage._mirror_backup_row)))
    assert not any(
        isinstance(node, ast.Constant) and node.value == "kobo"
        for node in ast.walk(tree)
    )


@pytest.mark.parametrize("statuses", (
    ("pending", "complete", "acquiring", "error"),
    ("complete", "reconcile_required", "complete"),
    ("ok", "pending_acquisition", "error"),
))
def test_w1_complete_archive_no_longer_writes_metadata_marker(tmp_path, statuses):
    """After archive-only cutover, no completed-history marker has a reader.

    An SQL trigger proves the real production writer cannot insert a new
    marker, even through first-complete, retry or interrupted transitions.
    """
    store = Storage(tmp_path / "state.sqlite")
    try:
        before = _snapshot(store)
        with store._lock, store._connect() as db:
            db.execute("""
                CREATE TRIGGER reject_obsolete_w1_marker
                BEFORE INSERT ON metadata
                WHEN NEW.key LIKE 'core.kobo.completed-legacy-archive.v1.%'
                BEGIN
                    SELECT RAISE(ABORT, 'obsolete_w1_marker_write');
                END
            """)
        for status in statuses:
            store.backup_put(_archive("vault", "alice", "buyer", status,
                                      sha="a" * 64))
            assert store.backup_get("kobo", "vault", "book")["status"] == status
            assert _snapshot(store) == before
        with store._lock, store._connect() as db:
            assert db.execute(
                "SELECT key FROM metadata WHERE key LIKE "
                "'core.kobo.completed-legacy-archive.v1.%'"
            ).fetchall() == []
    finally:
        store.close()


def test_w1_existing_obsolete_marker_is_preserved_but_never_authoritative(tmp_path):
    """Old metadata remains for rollback/audit, not admission or B proof."""
    store = Storage(tmp_path / "state.sqlite")
    digest = hashlib.sha256(json.dumps(
        ["vault", "book"], ensure_ascii=True, separators=(",", ":"),
    ).encode()).hexdigest()
    key = "core.kobo.completed-legacy-archive.v1." + digest
    try:
        with store._lock, store._connect() as db:
            db.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?)",
                (key, "archive_only_after_complete"),
            )
            initial_metadata = tuple(db.execute(
                "SELECT key,value FROM metadata ORDER BY key"
            ).fetchall())
        baseline = _snapshot(store)
        for status in ("acquiring", "error", "complete", "pending_acquisition"):
            store.backup_put(_archive("vault", "alice", "buyer", status))
            assert _snapshot(store) == baseline
        with store._lock, store._connect() as db:
            assert tuple(db.execute(
                "SELECT key,value FROM metadata ORDER BY key"
            ).fetchall()) == initial_metadata
    finally:
        store.close()
