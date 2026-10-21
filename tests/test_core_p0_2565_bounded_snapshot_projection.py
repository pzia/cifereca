"""#2565: bounded SQLite projection under active WAL and heavy metadata.

Synthetic rows only. No source mutation, remote calls, cache or repair.
"""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

from cifereca.core.library_reconciliation import (
    _query_image, read_library_snapshot,
)
from tests.test_library_reconciliation import fixture_db, filesystem


@pytest.fixture
def populated(tmp_path):
    path = tmp_path / "PRIVATE.sqlite"
    fixture_db(path)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA wal_autocheckpoint=0")
    # 4,096 local notices with realistic nonempty OPF-derived metadata.
    # A representative 522-row subset remains within the same path.
    rows = [
        (f"notice-{i:05}", "alice", "kobo", f"item-{i:05}",
         json.dumps({"identifiers": [{"scheme": "isbn13", "value": "9780306406157"}],
                     "title": f"PRIVATE_TITLE_{i:05}"}))
        for i in range(4096)
    ]
    db.executemany(
        "INSERT INTO catalog_entries "
        "(catalog_entry_id, identity_id, provider_id, item_id, metadata_json) "
        "VALUES (?, ?, ?, ?, ?)", rows,
    )
    db.execute(
        "INSERT INTO entitlements "
        "(entitlement_id,identity_id,provider_id,credential_id,account_id,item_id,state,"
        "entitlement_type,metadata_json) VALUES (?,?,?,?,?,?,?,?,?)",
        ("right-1", "alice", "kobo", "credential", "account", "item-1",
         "active", "owned", json.dumps({"source": "kobo.personal_manifest"})),
    )
    db.commit()
    try:
        yield path, db, tmp_path
    finally:
        db.close()


def test_streamed_projection_preserves_wal_rows_and_does_not_write(populated):
    path, _db, directory = populated
    before = filesystem(directory)
    result = read_library_snapshot(path, seconds=20)
    assert not result.reasons
    assert len(result.tables["catalog_entries"]) == 4096
    assert len(result.tables["entitlements"]) == 1
    assert result.tables["entitlements"][0]["attested_source"] == "kobo.personal_manifest"
    assert "metadata_json" not in result.tables["entitlements"][0]
    assert result.tables["catalog_entries"][0]["bibliographic_identifiers"] == (
        ("isbn13", "9780306406157"),
    )
    assert "metadata_json" not in result.tables["catalog_entries"][0]
    assert filesystem(directory) == before
    assert "PRIVATE_TITLE" not in repr(result)


def test_row_budget_refuses_completeness_without_looking_past_limit(populated):
    path, _db, directory = populated
    before = filesystem(directory)
    result = read_library_snapshot(path, max_rows=128, seconds=20)
    assert result.reasons == {"snapshot_row_budget"}
    # One entitlement consumes one of the global 128 rows, without a
    # fabricated rights/placement association for omitted catalog entries.
    assert len(result.tables["entitlements"]) == 1
    assert len(result.tables["catalog_entries"]) == 127
    assert result.tables["entitlements"][0]["attested_source"] == "kobo.personal_manifest"
    assert filesystem(directory) == before


def test_cancelled_streaming_projection_drops_all_partial_evidence(populated):
    path, _db, directory = populated
    with sqlite3.connect(path) as source:
        # A private copied image is used; never query the mutable source from
        # the reconciliation code under test.
        image = source.serialize()
    calls = [0]
    def cancelled():
        calls[0] += 1
        return calls[0] >= 12
    before = filesystem(directory)
    result = _query_image(image, max_rows=10_000,
                          deadline=time.monotonic() + 20, cancelled=cancelled)
    assert result.reasons == {"snapshot_cancelled"}
    assert result.tables == {}
    assert filesystem(directory) == before


def test_large_projection_revalidates_scope_in_batches_not_for_each_row(populated):
    path, _db, directory = populated
    # This test exercises the private projected image, not a disk SQLite
    # read by the Core. The caller callback can recheck config/identity and
    # roots; invoking it thousands of times materially delays an audit.
    with sqlite3.connect(path) as source:
        image = source.serialize()
    calls = [0]
    def still_active():
        calls[0] += 1
        return False
    result = _query_image(image, max_rows=10_000,
                          deadline=time.monotonic() + 20, cancelled=still_active)
    assert not result.reasons
    assert len(result.tables["catalog_entries"]) == 4096
    assert calls[0] < 700  # SQLite progress handler + max 64-row batches
