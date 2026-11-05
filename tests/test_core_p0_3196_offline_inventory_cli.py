"""P0 #3196: user can inventory the exact legacy Kobo cohort offline.

All SQLite content is synthetic. The CLI may not initialize Storage/Core,
upgrade schema, create a database or access the provider/network.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from cifereca import cli
from cifereca.core import legacy_backup_inventory_cli as offline
from cifereca.storage import Storage


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "PRIVATE-owner-credential.sqlite3"
    store = Storage(path)
    try:
        store.identity_put({
            "identity_id": "alice", "label": "PRIVATE_NAME",
            "directory_name": "alice",
        })
        store.identity_put({
            "identity_id": "bob", "label": "PRIVATE_OTHER",
            "directory_name": "bob",
        })
        for profile, card, item in (
            ("alice", "PRIVATE_CARD", "PRIVATE_EDITION"),
            ("bob", "PRIVATE_CARD_B", "PRIVATE_EDITION_B"),
        ):
            store.backup_put({
                "provider_id": "kobo", "identity_id": profile,
                "credential_id": card, "account_id": "PRIVATE_BORROWER",
                "item_id": item, "revision_id": "rev-1",
                "title": "PRIVATE_TITLE", "status": "complete",
                "local_path": None,
            })
    finally:
        store.close()
    return path


def test_offline_cli_uses_existing_sqlite_without_starting_core(
    tmp_path, monkeypatch, capsys,
):
    db = _database(tmp_path)
    monkeypatch.setattr(offline, "database_file", lambda create=False: db)
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **kwargs: pytest.fail("must not construct Core or plugins"),
    )
    monkeypatch.setattr(
        Storage, "__init__",
        lambda *args, **kwargs: pytest.fail("must not migrate SQLite"),
    )
    assert cli.main(["legacy-backup-inventory", "--identity", "alice", "--json"]) == 0
    printed = capsys.readouterr().out
    summary = json.loads(printed)
    assert summary["status"] == "success"
    assert summary["inventory"]["status"] == "complete"
    assert summary["inventory"]["scanned"] == 1
    assert summary["inventory"]["receipt_unproven"] == 1
    assert summary["inventory"]["item_id_unqualified"] == 0
    for secret in ("PRIVATE_NAME", "PRIVATE_CARD", "PRIVATE_EDITION",
                   "PRIVATE_BORROWER", "PRIVATE_TITLE", str(tmp_path), "bob"):
        assert secret not in printed
    # Existing cohort and SQLite schema were left unchanged.
    with sqlite3.connect(db) as check:
        assert check.execute(
            "SELECT COUNT(*) FROM backup_items",
        ).fetchone()[0] == 2


def test_cli_unknown_profile_refuses_with_no_cross_identity_totals(
    tmp_path, monkeypatch, capsys,
):
    db = _database(tmp_path)
    monkeypatch.setattr(offline, "database_file", lambda create=False: db)
    assert cli.main([
        "legacy-backup-inventory", "--identity", "missing", "--json",
    ]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed", "code": "invalid_scope",
    }


def test_invalid_selector_fails_before_attempting_to_open_database(
    monkeypatch, capsys,
):
    monkeypatch.setattr(
        offline, "inventory_existing_legacy_kobo",
        lambda **kw: pytest.fail("must not open SQLite"),
    )
    for args in (
        ["legacy-backup-inventory"],
        ["--account", "SECRET", "legacy-backup-inventory", "--identity", "alice"],
    ):
        assert cli.main(args) == 2
        assert json.loads(capsys.readouterr().out) == {
            "status": "failed", "code": "identity_scope_required",
        }


def test_missing_database_is_never_created_or_reported_as_empty(
    tmp_path, monkeypatch, capsys,
):
    missing = tmp_path / "no-profile.sqlite3"
    monkeypatch.setattr(offline, "database_file", lambda create=False: missing)
    assert cli.main([
        "legacy-backup-inventory", "--identity", "alice", "--json",
    ]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed", "code": "unavailable",
    }
    assert not missing.exists()


def test_old_database_is_not_upgraded_for_offline_inventory(
    tmp_path, monkeypatch, capsys,
):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
        db.execute(
            "INSERT INTO metadata VALUES('schema_version','13')"
        )
    monkeypatch.setattr(offline, "database_file", lambda create=False: path)
    assert cli.main([
        "legacy-backup-inventory", "--identity", "alice", "--json",
    ]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "failed", "code": "schema_unavailable",
    }
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT value FROM metadata WHERE key='schema_version'",
        ).fetchone() == ("13",)


@pytest.mark.parametrize("invalid", ["", "bad\nidentity", "x" * 1025])
def test_bounded_scope_refused_by_direct_offline_entry(tmp_path, invalid):
    db = _database(tmp_path)
    report = offline.inventory_existing_legacy_kobo(
        identity_id=invalid, database_path=db,
    )
    assert report["status"] == "invalid_scope"
    assert report["scanned"] == 0
