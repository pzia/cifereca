"""P0 #3196: exact publication proof is never inferred from old backup rows.

Synthetic v14 SQLite and PDF bytes only. No provider, ADEPT or unlink on
the production code path. The review does not mint a migration plan/token.
"""
import json

import pytest

from cifereca.core.legacy_backup_publication_review import (
    review_legacy_backup_publication,
)
from cifereca.core.canonical_recovery import _scope, _stage_id
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, publish, register, rows, store, target,
)


@pytest.fixture
def legacy_rig(store, tmp_path):
    root = tmp_path / "EBooks"
    source, snapshot = new_source(
        "A", provider="kobo", credential="card-a", account="buyer",
        edition="book",
    )
    register(store, "A", "kobo", "card-a", account="buyer")
    file = target(tmp_path, "A")
    publication = publish(store, root, file, source, snapshot)
    stage_id = _stage_id(source, "synthetic-selected-offer")
    receipt = {
        "schema": "canonical.completed-delivery.v1",
        "source": _scope(source),
        "offer_id": "synthetic-selected-offer",
        "path": str(file),
        "sha256": SHA,
        "receipt": "synthetic-completed-delivery",
        "adept_receipt": "",
    }
    with store._lock, store._connect() as db:
        db.execute(
            "INSERT INTO backup_items("
            "provider_id,credential_id,item_id,revision_id,title,status,"
            "local_path,size,sha256,identity_id,account_id,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("kobo", "card-a", "book", "revision-1", "Synthetic", "complete",
             str(file), len(BYTES), SHA, "A", "buyer", 1),
        )
        db.execute(
            "INSERT INTO acquisition_ledger("
            "acquisition_id,provider_id,credential_id,account_id,item_id,"
            "identity_id,status,artifact_id,metadata_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (stage_id, "kobo", "card-a", "buyer", "book", "A", "complete",
             publication.artifact_id, json.dumps(receipt), 1, 1),
        )
    return store, root, file, publication, stage_id


def _review(rig):
    db, root, _path, _pub, _stage = rig
    return review_legacy_backup_publication(
        db, library_root=root, identity_id="A",
        credential_id="card-a", item_id="book",
    )


def _counts(store):
    with store._lock, store._connect() as db:
        return tuple(
            db.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
            for name in ("backup_items", "acquisition_ledger",
                         "artifacts", "placements",
                         "source_references", "origin_links",
                         "provider_connections", "provider_accounts")
        )


def test_exact_same_account_canonical_receipt_and_bytes_are_readonly_attested(
    legacy_rig,
):
    db, _root, file, _pub, _stage = legacy_rig
    before = _counts(db)
    result = _review(legacy_rig)
    assert result.status == "verified_existing_publication"
    assert result.reason == "exact_canonical_source_and_bytes_observed"
    assert result.read_only
    assert result.may_migrate_or_retire is False
    assert _review(legacy_rig) == result
    assert _counts(db) == before
    assert file.read_bytes() == BYTES
    assert rows(db) == (1, 1, 1, 1)


@pytest.mark.parametrize("condition,reason", [
    ("missing_backup", "backup_missing"),
    ("no_revision", "revision_unverified"),
    ("wrong_status", "backup_not_complete"),
    ("other_account", "borrower_unverified"),
    ("other_owner", "account_scope_unverified"),
    ("disabled_connection", "connection_unverified"),
    ("other_connection_owner", "connection_unverified"),
    ("missing_borrower", "borrower_unverified"),
    ("wrong_sha", "canonical_placement_unverified"),
    ("wrong_size", "artifact_unverified"),
    ("missing_file", "physical_unverified"),
    ("altered_file", "physical_unverified"),
    ("symlink_file", "physical_unverified"),
    ("missing_placement", "canonical_placement_unverified"),
    ("other_placement_owner", "canonical_placement_unverified"),
    ("unpublished_origin", "canonical_receipt_unverified"),
    ("missing_receipt", "canonical_receipt_unverified"),
    ("ledger_path_collision", "canonical_receipt_unverified"),
    ("shared_backup_path", "shared_backup_path"),
    ("invalid_profile_dir", "profile_unverified"),
    ("malformed_receipt", "canonical_receipt_unverified"),
])
def test_unproven_legacy_source_or_physical_path_never_binds(
    legacy_rig, condition, reason, tmp_path,
):
    db, root, file, pub, stage_id = legacy_rig
    if condition == "missing_file":
        file.unlink()
    elif condition == "altered_file":
        file.write_bytes(BYTES[:-1] + b"X")
    elif condition == "symlink_file":
        other = file.with_name("independent.pdf")
        other.write_bytes(BYTES)
        file.unlink()
        file.symlink_to(other)
    else:
        with db._lock, db._connect() as connection:
            if condition == "missing_backup":
                connection.execute("DELETE FROM backup_items")
            elif condition == "no_revision":
                connection.execute("UPDATE backup_items SET revision_id=NULL")
            elif condition == "wrong_status":
                connection.execute(
                    "UPDATE backup_items SET status='acquiring'"
                )
            elif condition == "other_account":
                connection.execute(
                    "UPDATE backup_items SET account_id='other-buyer'"
                )
            elif condition == "other_owner":
                connection.execute(
                    "UPDATE backup_items SET identity_id='B'"
                )
            elif condition == "disabled_connection":
                connection.execute(
                    "UPDATE provider_connections SET enabled=0 "
                    "WHERE provider_id='kobo'"
                )
            elif condition == "other_connection_owner":
                connection.execute(
                    "UPDATE provider_connections SET identity_id='B' "
                    "WHERE provider_id='kobo'"
                )
            elif condition == "missing_borrower":
                connection.execute("DELETE FROM provider_accounts")
            elif condition == "wrong_sha":
                connection.execute(
                    "UPDATE backup_items SET sha256=?", ("f" * 64,)
                )
            elif condition == "wrong_size":
                connection.execute(
                    "UPDATE backup_items SET size=size+1"
                )
            elif condition == "missing_placement":
                connection.execute(
                    "UPDATE placements SET state='missing' "
                    "WHERE path=?", (str(file),)
                )
            elif condition == "other_placement_owner":
                connection.execute(
                    "UPDATE placements SET identity_id='B' "
                    "WHERE path=?", (str(file),)
                )
            elif condition == "unpublished_origin":
                connection.execute("DELETE FROM origin_links")
            elif condition == "missing_receipt":
                connection.execute(
                    "DELETE FROM acquisition_ledger WHERE acquisition_id=?",
                    (stage_id,),
                )
            elif condition == "ledger_path_collision":
                connection.execute(
                    "INSERT INTO acquisition_ledger("
                    "acquisition_id,provider_id,credential_id,account_id,"
                    "item_id,identity_id,status,metadata_json,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("other-stage", "kobo", "other-card", "other-account",
                     "other-book", "B", "pending", json.dumps({
                         "nested": {"path": str(file)},
                     }), 1, 1),
                )
            elif condition == "shared_backup_path":
                connection.execute(
                    "INSERT INTO backup_items("
                    "provider_id,credential_id,item_id,title,status,local_path,"
                    "updated_at) VALUES(?,?,?,?,?,?,?)",
                    ("kobo", "other-card", "other-book", "Other", "pending",
                     str(file), 1),
                )
            elif condition == "invalid_profile_dir":
                # UNIQUE(directory_name) already prevents a second identity
                # from claiming the same folder; test a reachable invalid one.
                connection.execute(
                    "UPDATE identities SET directory_name='..' "
                    "WHERE identity_id='A'"
                )
            elif condition == "malformed_receipt":
                connection.execute(
                    "UPDATE acquisition_ledger SET metadata_json=? "
                    "WHERE acquisition_id=?",
                    ('{"path":"one","path":"two"}', stage_id),
                )
    outcome = _review(legacy_rig)
    assert outcome.status == "blocked", condition
    assert outcome.reason == reason, condition
    assert outcome.read_only and not outcome.may_migrate_or_retire
    assert db.placement_get(pub.placement_id) is not None
    if condition not in ("missing_file", "altered_file", "symlink_file"):
        assert file.read_bytes() == BYTES


@pytest.mark.parametrize("bad", [
    "", "A" + chr(0), "A" + chr(10), None,
])
def test_invalid_identity_is_refused_without_sql_or_file_access(legacy_rig, bad):
    db, root, _file, _pub, _stage = legacy_rig
    outcome = review_legacy_backup_publication(
        db, library_root=root, identity_id=bad,
        credential_id="card-a", item_id="book",
    )
    assert outcome.status == "blocked"
    assert outcome.reason == "invalid_request"
