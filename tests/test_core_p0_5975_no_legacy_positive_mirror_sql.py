"""P0 #5975: no dormant positive W1/v3 SQL can be reactivated through archive APIs.

The live W1 and historical v2->v3 callers must never mint rights, physical
presence or catalogue state from a v0.7 backup receipt. No plugin I/O.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from cifereca.storage import Storage


def test_historical_mirror_has_no_positive_canonical_sql_left():
    source = inspect.getsource(Storage._mirror_backup_row)
    tree = ast.parse(textwrap.dedent(source))
    sql_literals = {
        node.value.casefold() for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and type(node.value) is str
    }
    for table in (
        "entitlements", "acquisition_ledger", "artifacts",
        "placements", "catalog_entries", "source_references", "origin_links",
    ):
        assert not any("insert into " + table in fragment for fragment in sql_literals)
        assert not any("update " + table in fragment for fragment in sql_literals)
        assert not any("delete from " + table in fragment for fragment in sql_literals)
    assert "backup_legacy_canonical_projection_retired" in source


def test_public_archive_call_and_v3_upgrade_cannot_enter_positive_mirror():
    source = inspect.getsource(Storage)
    tree = ast.parse(textwrap.dedent(source))
    functions = {
        node.name: node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    put_calls = [
        node for node in ast.walk(functions["backup_put"])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_mirror_backup_row"
    ]
    assert len(put_calls) == 1
    assert any(
        keyword.arg == "archive_only"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in put_calls[0].keywords
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_mirror_backup_row"
        for node in ast.walk(functions["_migrate"])
    )


def test_accidental_old_canonical_mirror_direct_invocation_fails_closed(tmp_path):
    store = Storage(tmp_path / "archive.sqlite")
    try:
        row = {
            "provider_id": "synthetic", "credential_id": "card",
            "account_id": "borrower", "identity_id": "alice",
            "item_id": "edition", "entitlement_type": "owned",
            "status": "complete", "sha256": "a" * 64,
            "local_path": "/unverified/not-verified.epub",
        }
        with store._lock, store._connect() as db:
            with pytest.raises(
                ValueError, match="backup_legacy_canonical_projection_retired"
            ):
                store._mirror_backup_row(db, row)
            for table in (
                "entitlements", "acquisition_ledger", "artifacts",
                "placements", "catalog_entries",
            ):
                assert db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
        assert store.backup_list() == []
        store.backup_put(row)
        assert store.backup_get("synthetic", "card", "edition") is not None
        assert store.entitlements() == []
        assert store.acquisitions() == []
    finally:
        store.close()
