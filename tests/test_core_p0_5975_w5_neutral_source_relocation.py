"""P0 #5975/#3196: W5 protects any typed source, not only Kobo.

These tests use disposable SQLite, local synthetic paths and no provider GET.
The direct Storage method updates metadata only; filesystem moving is owned
by the separately guarded filename-normalization broker.
"""
from __future__ import annotations

import ast

import pytest

from cifereca.storage import Storage
from tests.test_core041_artifact_authority import make_service


PROVIDERS = ("kobo", "paris", "numilog", "unregistered-provider")


def _case(tmp_path, provider):
    service, _, placement_id = make_service(tmp_path)
    store = service.storage
    old = tmp_path / "library" / "Book.epub"
    new = tmp_path / "library" / "Author - Book.epub"
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE placements SET provider_id=?,source_item_id=? WHERE placement_id=?",
            (provider, "edition-1", placement_id),
        )
        db.execute(
            "UPDATE catalog_entries SET provider_id=?,item_id=? WHERE placement_id=?",
            (provider, "edition-1", placement_id),
        )
        db.execute(
            "INSERT INTO backup_items("
            "provider_id,credential_id,item_id,title,status,updated_at,"
            "identity_id,account_id,local_path) VALUES(?,?,?,?,?,?,?,?,?)",
            (provider, "credential-1", "edition-1", "Historical", "complete",
             17, "id1", "unverified-borrower", str(old)),
        )
    return store, placement_id, old, new


def _typed_source(store, provider, *, credential="credential-1",
                  edition="edition-1", identity="id1", account="other-borrower"):
    with store._lock, store._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,"
            "account_id,branch_id,edition_id,right_id,reference_kind,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("typed-test", identity, provider, credential, account,
             "purchases", edition, "purchase", "purchase", 1, 1),
        )


def _snapshot(store):
    with store._lock, store._connect() as db:
        return tuple(
            (name, tuple(db.execute(
                "SELECT * FROM " + name + " ORDER BY 1"
            ).fetchall()))
            for name in (
                "backup_items", "placements", "catalog_entries",
                "source_references", "origin_links", "acquisition_ledger",
            )
        )


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("identity,account", [
    ("id1", "other-borrower"),
    ("another-profile", "foreign-borrower"),
])
def test_typed_source_blocks_legacy_relocation_for_any_provider(
    tmp_path, provider, identity, account,
):
    store, placement_id, old, new = _case(tmp_path, provider)
    try:
        _typed_source(store, provider, identity=identity, account=account)
        before = _snapshot(store)
        with pytest.raises(
            ValueError, match="^relocate_canonical_source_requires_core_relink$"
        ):
            store.relocate_local_path(
                old, new, identity_id="id1", provider_id=provider,
            )
        assert _snapshot(store) == before
        assert store.placement_get(placement_id)["path"] == str(old)
        assert old.read_bytes() == b"book-data"
        assert not new.exists()
    finally:
        store.close()


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("unrelated", ["other-provider", "other-credential", "other-edition"])
def test_unrelated_typed_source_does_not_block_scoped_canonical_relocation(
    tmp_path, provider, unrelated,
):
    store, placement_id, old, new = _case(tmp_path, provider)
    try:
        _typed_source(
            store,
            ("another-provider" if unrelated == "other-provider" else provider),
            credential=("other-credential" if unrelated == "other-credential"
                        else "credential-1"),
            edition=("other-edition" if unrelated == "other-edition"
                     else "edition-1"),
        )
        with store._lock, store._connect() as db:
            legacy = db.execute("SELECT * FROM backup_items").fetchall()
            db.execute(
                "CREATE TRIGGER forbid_archive_writer BEFORE UPDATE OF local_path "
                "ON backup_items BEGIN SELECT RAISE(ABORT, 'archive_changed'); END"
            )
        store.relocate_local_path(
            old, new, identity_id="id1", provider_id=provider,
        )
        assert store.placement_get(placement_id)["path"] == str(new)
        linked = [
            row for row in store.catalog_entries()
            if row["placement_id"] == placement_id
        ]
        assert len(linked) == 1
        assert linked[0]["metadata"]["path"] == str(new)
        with store._lock, store._connect() as db:
            assert db.execute("SELECT * FROM backup_items").fetchall() == legacy
    finally:
        store.close()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_noop_relocation_keeps_attested_source_and_historical_path(tmp_path, provider):
    store, placement_id, old, new = _case(tmp_path, provider)
    try:
        _typed_source(store, provider)
        before = _snapshot(store)
        store.relocate_local_path(old, old, identity_id="id1", provider_id=provider)
        assert _snapshot(store) == before
        assert store.placement_get(placement_id)["path"] == str(old)
    finally:
        store.close()


def test_source_guard_has_no_supplier_specific_ast_decision():
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / "src/cifereca/storage.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "relocate_local_path")
    vendor_literals = {"kobo", "paris", "numilog", "bnf"}
    assert not any(
        isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value.casefold() in vendor_literals
        for node in ast.walk(method)
    )
