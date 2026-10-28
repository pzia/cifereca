"""#3196 / #3171 — schema v14 is additive, empty and scope-safe.

These synthetic SQL tests DO NOT claim an acquisition or verify local bytes.
The migration must not promote 0.7 backup rows or ledger receipts into
SourceReference/OriginLink without exact account, branch and final SHA proof.
"""
from pathlib import Path
import sqlite3

import pytest

from cifereca.storage import Storage, CURRENT_SCHEMA_VERSION


SHA = "a" * 64


def _tables(db):
    return {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}


def _ref(db, ref, *, identity="A", provider="seller-A", credential="card-A",
         account="buyer-A", branch="purchases", edition="edition-1",
         right="purchase-1", kind="purchase", synchronizable=0):
    db.execute(
        "INSERT INTO source_references("
        "source_reference_id,identity_id,provider_id,credential_id,account_id,"
        "branch_id,edition_id,right_id,reference_kind,synchronizable,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (ref, identity, provider, credential, account, branch, edition,
         right, kind, synchronizable, 1, 1),
    )


def _link(db, link, kind, profile, *, source=None, parent=None,
          from_identity=None):
    db.execute(
        "INSERT INTO origin_links("
        "origin_link_id,origin_kind,identity_id,artifact_id,"
        "source_reference_id,evidence_ref,parent_artifact_id,"
        "from_identity_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (link, kind, profile, "sha256:" + SHA, source,
         "synthetic-proof", parent, from_identity, 1, 1),
    )


def test_fresh_schema_is_empty_and_legacy_tables_still_exist(tmp_path):
    storage = Storage(tmp_path / "reader.sqlite")
    try:
        assert CURRENT_SCHEMA_VERSION == storage.schema_version() == 14
        with storage._lock, storage._connect() as db:
            assert {"source_references", "origin_links", "backup_items",
                    "entitlements", "artifacts", "placements",
                    "acquisition_ledger"} <= _tables(db)
            assert db.execute("SELECT count(*) FROM source_references").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM origin_links").fetchone()[0] == 0
            sql = {row[1] for row in db.execute("PRAGMA index_list(source_references)")}
            assert "idx_source_references_scope" in sql
    finally:
        storage.close()


def test_v13_migration_does_not_invent_provider_origins_from_old_backup(tmp_path):
    path = tmp_path / "legacy.sqlite"
    storage = Storage(path)
    old = {
        "identity_id": "A", "provider_id": "opaque-old-seller",
        "credential_id": "old-cred", "account_id": "old-buyer",
        "item_id": "old-item", "title": "Same title not proof",
        "status": "complete", "entitlement_type": "owned",
    }
    try:
        storage.backup_put(old)
        before_rights = storage.entitlements()
        before_ledger = storage.acquisitions()
        before_backup = storage.backup_list()
    finally:
        storage.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE origin_links")
        db.execute("DROP TABLE source_references")
        db.execute("UPDATE metadata SET value='13' WHERE key='schema_version'")
    migrated = Storage(path)
    try:
        assert migrated.schema_version() == 14
        assert migrated.entitlements() == before_rights
        assert migrated.acquisitions() == before_ledger
        assert migrated.backup_list() == before_backup
        with migrated._lock, migrated._connect() as db:
            assert db.execute("SELECT count(*) FROM source_references").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM origin_links").fetchone()[0] == 0
        migrated.close()
        reopened = Storage(path)
        try:
            assert reopened.schema_version() == 14
            assert reopened.backup_list() == before_backup
        finally:
            reopened.close()
    finally:
        migrated.close()


def test_two_providers_two_profiles_and_two_branches_never_coalesce(tmp_path):
    storage = Storage(tmp_path / "scope.sqlite")
    try:
        with storage._lock, storage._connect() as db:
            _ref(db, "source-a")
            _ref(db, "source-b", identity="B", provider="seller-B",
                 credential="card-B", account="buyer-B")
            _ref(db, "source-dilicom", branch="dilicom")
            _ref(db, "source-syracuse", branch="syracuse")
            assert db.execute("SELECT count(*) FROM source_references").fetchone()[0] == 4
            with pytest.raises(sqlite3.IntegrityError):
                _ref(db, "duplicate-coordinate")
            # A different branch is distinct even with same exact item/right.
            assert db.execute(
                "SELECT count(*) FROM source_references WHERE identity_id='A'"
            ).fetchone()[0] == 3
            assert db.execute("SELECT count(*) FROM entitlements").fetchone()[0] == 0
    finally:
        storage.close()


def test_eight_contextual_links_refer_to_single_artifact_not_eight_rights(tmp_path):
    storage = Storage(tmp_path / "origin.sqlite")
    try:
        storage.artifact_put({
            "artifact_id": "sha256:" + SHA, "sha256": SHA,
            "size": 1234, "media_format": "epub",
        })
        with storage._lock, storage._connect() as db:
            _ref(db, "right-A")
            _ref(db, "right-B", identity="B", provider="seller-B",
                 credential="card-B", account="buyer-B")
            _ref(db, "internal-B", identity="B", provider="cifereca",
                 credential="local", account="B", branch="transfer",
                 kind="internal", synchronizable=0)
            _ref(db, "internal-C", identity="C", provider="cifereca",
                 credential="local", account="C", branch="transfer",
                 kind="internal", synchronizable=0)
            _link(db, "A-provider", "provider", "A", source="right-A")
            _link(db, "A-adept", "adept", "A", source="right-A")
            _link(db, "A-to-B-transfer", "transfer", "B", from_identity="A")
            _link(db, "A-to-B-cifereca", "cifereca", "B", source="internal-B")
            _link(db, "B-provider", "provider", "B", source="right-B")
            _link(db, "B-adept", "adept", "B", source="right-B")
            _link(db, "B-to-C-transfer", "transfer", "C", from_identity="B")
            _link(db, "B-to-C-cifereca", "cifereca", "C", source="internal-C")
            assert db.execute("SELECT count(*) FROM origin_links").fetchone()[0] == 8
            assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM entitlements").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM placements").fetchone()[0] == 0
            assert db.execute(
                "SELECT count(*) FROM source_references "
                "WHERE reference_kind='internal' AND synchronizable=0"
            ).fetchone()[0] == 2
            with pytest.raises(sqlite3.IntegrityError):
                _link(db, "A-to-B-transfer", "transfer", "B", from_identity="A")
            with pytest.raises(sqlite3.IntegrityError):
                _link(db, "unproven-provider", "provider", "C")
            with pytest.raises(sqlite3.IntegrityError):
                _link(db, "unparented-derived", "derived", "C")
            assert db.execute("SELECT count(*) FROM origin_links").fetchone()[0] == 8
    finally:
        storage.close()


def test_failed_migration_does_not_leave_partly_created_authority(tmp_path, monkeypatch):
    path = tmp_path / "migration.sqlite"
    storage = Storage(path)
    storage.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE origin_links")
        db.execute("DROP TABLE source_references")
        db.execute("UPDATE metadata SET value='13' WHERE key='schema_version'")
    before = Storage._write_schema_version

    def fail_on_v14(db, version):
        if version == 14:
            raise sqlite3.OperationalError("synthetic failure before version commit")
        return before(db, version)

    monkeypatch.setattr(Storage, "_write_schema_version", staticmethod(fail_on_v14))
    with pytest.raises(sqlite3.OperationalError, match="synthetic failure"):
        Storage(path)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0] == "13"
        assert "source_references" not in _tables(db)
        assert "origin_links" not in _tables(db)
    monkeypatch.setattr(Storage, "_write_schema_version", staticmethod(before))
    recovered = Storage(path)
    try:
        assert recovered.schema_version() == 14
    finally:
        recovered.close()


def test_provider_origin_must_have_matching_profile_and_source_type(tmp_path):
    storage = Storage(tmp_path / "provider-scope.sqlite")
    try:
        storage.artifact_put({
            "artifact_id": "sha256:" + SHA, "sha256": SHA,
            "size": 1234, "media_format": "epub",
        })
        with storage._lock, storage._connect() as db:
            _ref(db, "purchase-A")
            _ref(db, "purchase-B", identity="B", provider="seller-B",
                 credential="card-B", account="buyer-B")
            _ref(db, "transferred-C", identity="C", provider="cifereca",
                 credential="local", account="C", kind="internal")
            _link(db, "A-provider", "provider", "A", source="purchase-A")
            _link(db, "B-provider", "provider", "B", source="purchase-B")
            with pytest.raises(sqlite3.IntegrityError, match="provider_origin_scope_mismatch"):
                _link(db, "foreign", "provider", "B", source="purchase-A")
            with pytest.raises(sqlite3.IntegrityError, match="provider_origin_scope_mismatch"):
                _link(db, "internal-is-not-a-purchase", "provider", "C",
                      source="transferred-C")
            with pytest.raises(sqlite3.IntegrityError, match="provider_origin_scope_mismatch"):
                db.execute(
                    "UPDATE origin_links SET identity_id='C' WHERE origin_link_id='A-provider'"
                )
            with pytest.raises(sqlite3.IntegrityError, match="provider_origin_source_immutable"):
                db.execute(
                    "UPDATE source_references SET identity_id='C' "
                    "WHERE source_reference_id='purchase-A'"
                )
            assert db.execute("SELECT count(*) FROM origin_links").fetchone()[0] == 2
            assert db.execute(
                "SELECT identity_id FROM source_references "
                "WHERE source_reference_id='purchase-A'"
            ).fetchone()[0] == "A"
            # A per-profile purchase right is still independent at the same SHA.
            assert db.execute(
                "SELECT count(DISTINCT identity_id) FROM origin_links "
                "WHERE origin_kind='provider'"
            ).fetchone()[0] == 2
    finally:
        storage.close()


def test_origin_history_survives_parent_delete_attempts_even_without_fk_pragma(tmp_path):
    storage = Storage(tmp_path / "history.sqlite")
    try:
        storage.artifact_put({
            "artifact_id": "sha256:" + SHA, "sha256": SHA,
            "size": 1234, "media_format": "epub",
        })
        with storage._lock, storage._connect() as db:
            assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 0
            _ref(db, "right")
            _link(db, "provider-link", "provider", "A", source="right")
            with pytest.raises(sqlite3.IntegrityError, match="origin_artifact_missing"):
                db.execute(
                    "INSERT INTO origin_links("
                    "origin_link_id,origin_kind,identity_id,artifact_id,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    ("absent-artifact", "manual", "A", "sha256:" + "f" * 64, 1, 1),
                )
            with pytest.raises(sqlite3.IntegrityError, match="origin_artifact_still_referenced"):
                db.execute(
                    "DELETE FROM artifacts WHERE artifact_id=?",
                    ("sha256:" + SHA,),
                )
            with pytest.raises(sqlite3.IntegrityError, match="origin_source_still_referenced"):
                db.execute(
                    "DELETE FROM source_references WHERE source_reference_id='right'"
                )
            assert db.execute("SELECT count(*) FROM origin_links").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM source_references").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    finally:
        storage.close()


def test_transfer_and_internal_source_require_exact_profiles(tmp_path):
    storage = Storage(tmp_path / "transfer.sqlite")
    try:
        storage.artifact_put({
            "artifact_id": "sha256:" + SHA, "sha256": SHA,
            "size": 1234, "media_format": "epub",
        })
        with storage._lock, storage._connect() as db:
            _ref(db, "internal-B", identity="B", provider="cifereca",
                 credential="local", account="B", kind="internal")
            _ref(db, "internal-C", identity="C", provider="cifereca",
                 credential="local", account="C", kind="internal")
            _link(db, "A-to-B", "transfer", "B", from_identity="A")
            _link(db, "B-cifereca", "cifereca", "B", source="internal-B")
            with pytest.raises(sqlite3.IntegrityError):
                _link(db, "not-a-transfer", "transfer", "B")
            with pytest.raises(sqlite3.IntegrityError):
                _link(db, "same-profile", "transfer", "B", from_identity="B")
            with pytest.raises(sqlite3.IntegrityError, match="internal_origin_scope_mismatch"):
                _link(db, "foreign-internal", "cifereca", "B", source="internal-C")
            with pytest.raises(sqlite3.IntegrityError, match="internal_origin_source_immutable"):
                db.execute(
                    "UPDATE source_references SET identity_id='C' "
                    "WHERE source_reference_id='internal-B'"
                )
            with pytest.raises(sqlite3.IntegrityError, match="origin_artifact_sha_immutable"):
                db.execute(
                    "UPDATE artifacts SET sha256=? WHERE artifact_id=?",
                    ("f" * 64, "sha256:" + SHA),
                )
            assert db.execute("SELECT count(*) FROM origin_links").fetchone()[0] == 2
    finally:
        storage.close()
