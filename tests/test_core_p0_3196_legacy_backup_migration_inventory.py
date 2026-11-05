"""P0 #3196: read-only, complete-scope inventory before any legacy migration.

All rows, receipts and paths are synthetic. No network, hashing or deletion.
"""
import json

import pytest

from cifereca.core.legacy_backup_migration_inventory import (
    inventory_legacy_kobo_backups,
)
from cifereca.core.canonical_publication import _stable
from tests.test_core_p0_3171_atomic_publication_v14 import register, store


def _backup(storage, *, identity="A", credential="card-a", account="buyer",
            item="book", status="complete", revision="r1", path=None,
            sha=None, size=None):
    storage.backup_put({
        "provider_id": "kobo", "identity_id": identity,
        "credential_id": credential, "account_id": account,
        "item_id": item, "revision_id": revision, "title": "Synthetic",
        "status": status, "local_path": str(path) if path else None,
        "sha256": sha, "size": size,
    })


def _rows(storage):
    with storage._lock, storage._connect() as db:
        return tuple(
            db.execute(
                "SELECT COUNT(*),COALESCE(SUM(updated_at),0) FROM " + name
            ).fetchone()
            for name in ("backup_items", "acquisition_ledger",
                         "entitlements", "placements",
                         "origin_links", "source_references")
        )


def _report(storage, identity="A", max_rows=1000):
    return inventory_legacy_kobo_backups(
        storage, identity_id=identity, max_rows=max_rows,
    )


def test_inventory_classifies_all_historical_states_without_authorizing_migration(
    store, tmp_path,
):
    register(store, "A", "kobo", "card-a", account="buyer")
    _backup(store, item="acquiring", status="pending_acquisition")
    _backup(store, item="repair", status="reconcile_required")
    _backup(store, item="archived", status="skipped_archived")
    _backup(store, item="complete", status="complete",
            revision=None, path=tmp_path / "fictional.epub",
            sha="a" * 64, size=55)
    _backup(store, item="other", status="attention_required")
    before = _rows(store)
    result = _report(store)
    assert result["status"] == "complete"
    assert result["scanned"] == 5
    assert result["status_complete"] == 1
    assert result["status_acquiring"] == 1
    assert result["status_reconcile_required"] == 1
    assert result["status_skipped_archived"] == 1
    assert result["status_other"] == 1
    assert result["revision_unrecorded"] == 1
    assert result["ledger_absent"] == 0
    assert result["receipt_shape_candidate"] == 0
    assert result["receipt_unproven"] == 5
    assert "buyer" not in repr(result)
    assert str(tmp_path) not in repr(result)
    assert "may_migrate" not in result
    assert _report(store) == result
    assert _rows(store) == before
    assert not (tmp_path / "fictional.epub").exists()


def test_unscoped_row_anywhere_blocks_a_clean_report_for_other_profile(store):
    register(store, "A", "kobo", "card-a", account="buyer")
    register(store, "B", "kobo", "card-b", account="foreign")
    _backup(store, item="own")
    _backup(store, identity="B", credential="card-b", account="foreign",
            item="unscoped")
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE backup_items SET identity_id=NULL "
            "WHERE provider_id='kobo' AND item_id='unscoped'"
        )
    result = _report(store)
    assert result["status"] == "scope_unverified"
    assert all(value == 0 for key, value in result.items() if key != "status")


def test_budget_returns_no_partial_counts_and_other_identity_is_isolated(store):
    register(store, "A", "kobo", "card-a", account="buyer")
    register(store, "B", "kobo", "card-b", account="foreign")
    for item in ("one", "two"):
        _backup(store, item=item)
    _backup(store, identity="B", credential="card-b", account="foreign",
            item="three")
    before = _rows(store)
    limited = _report(store, max_rows=1)
    assert limited["status"] == "budget_exceeded"
    assert all(value == 0 for key, value in limited.items()
               if key != "status")
    assert _report(store, identity="B")["scanned"] == 1
    assert _report(store)["scanned"] == 2
    assert _rows(store) == before


@pytest.mark.parametrize("owner,cap", [
    ("", 1), (None, 1), ("A", 0), ("A", 1001),
    ("A", True), ("A" + chr(0) + "other", 2), ("A" + chr(10) + "other", 2),
])
def test_invalid_identity_or_budget_does_not_inventory(store, owner, cap):
    result = _report(store, identity=owner, max_rows=cap)
    assert result["status"] == "invalid_scope"
    assert result["scanned"] == 0


def test_foreign_connection_does_not_bind_legacy_account(store):
    register(store, "A", "kobo", "card-a", account="buyer")
    _backup(store)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE provider_connections SET identity_id='B' "
            "WHERE provider_id='kobo' AND credential_id='card-a'"
        )
    result = _report(store)
    assert result["status"] == "complete"
    assert result["account_unbound"] == 1
    assert result["account_conflicting"] == 1
    assert result["receipt_shape_candidate"] == 0
    assert result["receipt_unproven"] == 1


def test_exact_account_can_be_empty_but_not_null(store):
    register(store, "A", "kobo", "card-a", account="")
    _backup(store, account="")
    assert _report(store)["account_unbound"] == 0
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE backup_items SET account_id=NULL "
            "WHERE provider_id='kobo' AND item_id='book'"
        )
    assert _report(store)["account_unbound"] == 1
    assert _report(store)["receipt_shape_candidate"] == 0


def test_same_path_across_backup_rows_is_detected_but_never_relinked(
    store, tmp_path,
):
    register(store, "A", "kobo", "card-a", account="buyer")
    _backup(store, item="one")
    _backup(store, item="two")
    shared = str(tmp_path / "same.epub")
    with store._lock, store._connect() as db:
        db.execute("UPDATE backup_items SET local_path=?", (shared,))
    result = _report(store)
    assert result["status"] == "complete"
    assert result["path_shared"] == 2
    assert result["path_unrecorded"] == 0
    assert not (tmp_path / "same.epub").exists()
    with store._lock, store._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM backup_items WHERE local_path=?",
            (shared,),
        ).fetchone()[0] == 2


def test_ledger_ambiguous_or_rebound_never_promotes_backup(store):
    register(store, "A", "kobo", "card-a", account="buyer")
    _backup(store)
    with store._lock, store._connect() as db:
        db.execute(
            "INSERT INTO acquisition_ledger("
            "acquisition_id,provider_id,credential_id,account_id,item_id,"
            "identity_id,status,metadata_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("foreign-operation", "kobo", "card-a", "other-account",
             "book", "B", "complete", "{}", 1, 1),
        )
    report = _report(store)
    assert report["ledger_scope_conflicting"] == 1
    assert report["ledger_ambiguous"] == 1
    assert report["receipt_shape_candidate"] == 0


def test_checkpoint_syntax_is_never_a_published_origin_or_physical_proof(
    store, tmp_path,
):
    register(store, "A", "kobo", "card-a", account="buyer")
    path = tmp_path / "absent-but-claimed.epub"
    _backup(store, item="book", path=path, sha="a" * 64, size=42)
    payload = {
        "schema": "canonical.completed-delivery.v1",
        "source": [
            "A", "kobo", "card-a", "buyer", "purchases",
            "book", "purchase", "purchase",
        ],
        "offer_id": "synthetic-offer", "path": str(path),
        "sha256": "a" * 64, "receipt": "synthetic-receipt",
        "adept_receipt": "",
    }
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE acquisition_ledger SET status='complete',"
            "artifact_id=?,metadata_json=? "
            "WHERE provider_id='kobo' AND item_id='book'",
            ("sha256:" + "a" * 64, json.dumps(payload)),
        )
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE acquisition_ledger SET acquisition_id=? "
            "WHERE provider_id='kobo' AND item_id='book'",
            (_stable("canonical-intake", *payload["source"], payload["offer_id"]),),
        )
    original = _rows(store)
    result = _report(store)
    assert result["status"] == "complete"
    assert result["receipt_shape_candidate"] == 1
    assert result["receipt_unproven"] == 1
    # An absent file and nonexistent published OriginLink remain unproven.
    # The inventory deliberately has no commit or deletion authority.
    assert not path.exists()
    assert "migration_ready" not in result
    assert _rows(store) == original


def test_unrelated_operation_is_not_a_completed_receipt_candidate(store, tmp_path):
    register(store, "A", "kobo", "card-a", account="buyer")
    path = tmp_path / "never-published.epub"
    _backup(store, path=path, sha="a" * 64, size=42)
    source = ["A", "kobo", "card-a", "buyer", "purchases",
              "book", "purchase", "purchase"]
    checkpoint = {
        "schema": "canonical.completed-delivery.v1",
        "source": source, "offer_id": "expected-offer",
        "path": str(path), "sha256": "a" * 64,
        "receipt": "synthetic-receipt", "adept_receipt": "",
    }
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE acquisition_ledger SET acquisition_id=?, status='complete',"
            "artifact_id=?, metadata_json=? WHERE provider_id='kobo' "
            "AND item_id='book'",
            (_stable("canonical-intake", *source, "unrelated-offer"),
             "sha256:" + "a" * 64, json.dumps(checkpoint)),
        )
    before = _rows(store)
    result = _report(store)
    assert result["receipt_shape_candidate"] == 0
    assert result["receipt_unproven"] == 1
    assert _rows(store) == before
    assert not path.exists()


@pytest.mark.parametrize("legacy_status,revision", [
    ("pending_acquisition", "r1"),
    ("acquiring", "r1"),
    ("reconcile_required", "r1"),
    ("skipped_archived", "r1"),
    ("attention_required", "r1"),
    ("complete", None),
    ("complete", ""),
])
def test_completed_ledger_does_not_complete_legacy_backup_or_supply_revision(
    store, tmp_path, legacy_status, revision,
):
    register(store, "A", "kobo", "card-a", account="buyer")
    path = tmp_path / "absent.epub"
    _backup(store, item="book", status=legacy_status, revision=revision,
            path=path, sha="a" * 64, size=42)
    source = ["A", "kobo", "card-a", "buyer", "purchases",
              "book", "purchase", "purchase"]
    checkpoint = {
        "schema": "canonical.completed-delivery.v1",
        "source": source, "offer_id": "exact-offer",
        "path": str(path), "sha256": "a" * 64,
        "receipt": "synthetic-receipt", "adept_receipt": "",
    }
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE acquisition_ledger SET acquisition_id=?,status='complete',"
            "artifact_id=?,metadata_json=? WHERE provider_id='kobo' "
            "AND item_id='book'",
            (_stable("canonical-intake", *source, "exact-offer"),
             "sha256:" + "a" * 64, json.dumps(checkpoint)),
        )
    before = _rows(store)
    report = _report(store)
    assert report["status"] == "complete"
    assert report["receipt_shape_candidate"] == 0
    assert report["receipt_unproven"] == 1
    assert report["revision_unrecorded"] == int(not revision)
    assert _rows(store) == before
    assert not path.exists()


def test_malformed_duplicate_checkpoint_is_not_a_receipt(store, tmp_path):
    register(store, "A", "kobo", "card-a", account="buyer")
    path = tmp_path / "not-on-disk.epub"
    _backup(store, path=path, sha="a" * 64, size=12)
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE acquisition_ledger SET status='complete',"
            "artifact_id=?,metadata_json=? "
            "WHERE provider_id='kobo' AND item_id='book'",
            ("sha256:" + "a" * 64,
             '{"schema":"canonical.completed-delivery.v1",'
             '"path":"x","path":"y"}'),
        )
    report = _report(store)
    assert report["receipt_shape_candidate"] == 0
    assert report["receipt_unproven"] == 1


@pytest.mark.parametrize("unqualified", [
    "", "  ", " book ", "bad\x00id", "bad\nid", "bad\rid",
    "x" * 1025,
])
def test_inventory_counts_unqualified_legacy_item_ids_without_migration(
    store, unqualified,
):
    # SQLite 0.7 has no item-ID CHECK. A valid remote manifest cannot
    # reconstruct a unique canonical SourceKey for these historical rows.
    register(store, "A", "kobo", "card-a", account="buyer")
    _backup(store, item="clean")
    with store._lock, store._connect() as db:
        db.execute(
            "INSERT INTO backup_items("
            "provider_id,credential_id,item_id,title,status,updated_at,"
            "identity_id,account_id,revision_id"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            ("kobo", "card-a", unqualified, "Opaque legacy", "complete",
             1, "A", "buyer", "r1"),
        )
    before = _rows(store)
    report = _report(store)
    assert report["status"] == "complete"
    assert report["scanned"] == 2
    assert report["item_id_unqualified"] == 1
    assert report["account_unbound"] == 1
    assert report["receipt_shape_candidate"] == 0
    assert report["receipt_unproven"] == 2
    assert unqualified not in repr(report) or not unqualified
    assert _report(store) == report
    assert _rows(store) == before


def test_inventory_qualified_ids_have_no_unqualified_counter(store):
    register(store, "A", "kobo", "card-a", account="buyer")
    _backup(store, item="normal")
    before = _rows(store)
    report = _report(store)
    assert report["item_id_unqualified"] == 0
    assert report["account_unbound"] == 0
    assert report["scanned"] == 1
    assert _rows(store) == before
