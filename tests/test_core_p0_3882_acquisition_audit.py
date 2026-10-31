"""#3882 synthetic source/placement audit: no GET, write, merge or title matching."""
from __future__ import annotations

import hashlib
import sqlite3
from threading import RLock

import pytest

from cifereca.core.acquisition_audit import audit_account


SCHEMA = """
CREATE TABLE identities(identity_id TEXT, directory_name TEXT);
CREATE TABLE provider_connections(connection_id TEXT, identity_id TEXT, provider_id TEXT,
credential_id TEXT, enabled INTEGER);
CREATE TABLE provider_accounts(connection_id TEXT, identity_id TEXT, provider_id TEXT,
credential_id TEXT, account_id TEXT);
CREATE TABLE source_references(source_reference_id TEXT, identity_id TEXT, provider_id TEXT,
credential_id TEXT, account_id TEXT, edition_id TEXT, right_id TEXT,
reference_kind TEXT, state TEXT, synchronizable INTEGER);
CREATE TABLE origin_links(origin_link_id TEXT, origin_kind TEXT, identity_id TEXT,
source_reference_id TEXT, artifact_id TEXT);
CREATE TABLE artifacts(artifact_id TEXT, sha256 TEXT, size INTEGER);
CREATE TABLE placements(placement_id TEXT, identity_id TEXT, artifact_id TEXT,
path TEXT, state TEXT);
CREATE TABLE catalog_entries(catalog_entry_id TEXT, identity_id TEXT, provider_id TEXT,
item_id TEXT, placement_id TEXT, local_state TEXT);
CREATE TABLE acquisition_ledger(acquisition_id TEXT, identity_id TEXT, provider_id TEXT,
credential_id TEXT, account_id TEXT, status TEXT, artifact_id TEXT,
attempt_count INTEGER, error_code TEXT);
"""


class Storage:
    def __init__(self, path):
        self.path = path
        self._lock = RLock()

    def _connect(self):
        return sqlite3.connect(self.path)


@pytest.fixture
def setup_db(tmp_path):
    database = tmp_path / "audit.sqlite"
    with sqlite3.connect(database) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO identities VALUES('alice','Alice')")
        db.execute("INSERT INTO identities VALUES('bob','Bob')")
        db.execute("INSERT INTO provider_connections VALUES('c1','alice','numilog','cred1',1)")
        db.execute("INSERT INTO provider_accounts VALUES('c1','alice','numilog','cred1','accountA')")
        db.execute("INSERT INTO provider_connections VALUES('c2','bob','numilog','cred2',1)")
        db.execute("INSERT INTO provider_accounts VALUES('c2','bob','numilog','cred2','accountB')")
    root = tmp_path / "EBooks"
    (root / "Alice" / "numilog").mkdir(parents=True)
    staging = tmp_path / "staging"
    staging.mkdir()
    return Storage(database), root, staging


def insert_book(storage, root, staging, *, i, zone, origin=True, card=True):
    payload = ("book-data-%s" % i).encode()
    sha = hashlib.sha256(payload).hexdigest()
    artifact = "sha256:" + sha
    filename = ("book-%s.epub" % i)
    path = (root / "Alice" / "numilog" / filename) if zone == "final" else (staging / filename)
    if zone != "missing":
        path.write_bytes(payload)
    with storage._connect() as db:
        ref = "source:%s" % i
        db.execute(
            "INSERT INTO source_references VALUES(?,?,?,?,?,?,?,?,?,?)",
            (ref, "alice", "numilog", "cred1", "accountA",
             "edition-%s" % i, "right-%s" % i, "purchase", "active", 1),
        )
        db.execute("INSERT INTO artifacts VALUES(?,?,?)", (artifact, sha, len(payload)))
        if origin:
            db.execute("INSERT INTO origin_links VALUES(?,?,?,?,?)",
                       ("origin-%s" % i, "provider", "alice", ref, artifact))
            db.execute("INSERT INTO placements VALUES(?,?,?,?,?)",
                       ("placement-%s" % i, "alice", artifact, str(path), "present"))
            if card:
                db.execute("INSERT INTO catalog_entries VALUES(?,?,?,?,?,?)",
                           ("local-%s" % i, "alice", "local", None,
                            "placement-%s" % i, "local"))
        db.execute("INSERT INTO catalog_entries VALUES(?,?,?,?,?,?)",
                   ("remote-%s" % i, "alice", "numilog",
                    "right-%s" % i, None, "online"))
        db.execute("INSERT INTO acquisition_ledger VALUES(?,?,?,?,?,?,?,?,?)",
                   ("receipt-%s" % i, "alice", "numilog", "cred1",
                    "accountA", "complete", artifact, 1, None))
    return path


def audit(storage, root, staging, **kwargs):
    return audit_account(
        storage, identity_id="alice", provider_id="numilog",
        credential_id="cred1", account_id="accountA",
        library_root=root, staging_roots=(staging,), opaque_salt=b"0" * 16,
        **kwargs,
    )


def test_six_numilog_books_staging_vs_final_without_provenance_guess(setup_db):
    storage, root, staging = setup_db
    for i in range(6):
        insert_book(storage, root, staging, i=i, zone="final" if i < 2 else "staging")
    before = storage.path.read_bytes()
    result = audit(storage, root, staging, verify_files=True)
    assert storage.path.read_bytes() == before
    assert result["source_count"] == 6
    assert result["counts"]["complete_local"] == 2
    assert result["counts"]["staged_unpublished"] == 4
    assert result["counts"]["staged_visible_in_library"] == 4
    assert result["counts"]["published_linked_to_origin"] == 2
    assert result["counts"]["separate_local_and_remote_cards"] == 6
    assert all(row["match_basis"] == "origin_exact" for row in result["sources"])
    assert all(row["origin_link_account_match"] is True for row in result["sources"])
    serialized = __import__("json").dumps(result)
    for secret in ("cred1", "accountA", "Alice", "book-0.epub", "right-0"):
        assert secret not in serialized


def test_staged_receipt_without_origin_is_visible_but_not_published(setup_db):
    storage, root, staging = setup_db
    path = insert_book(storage, root, staging, i=17, zone="staging", origin=False)
    artifact = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    with storage._connect() as db:
        db.execute("INSERT INTO placements VALUES(?,?,?,?,?)",
                   ("orphan-17", "alice", artifact, str(path), "present"))
        db.execute("INSERT INTO catalog_entries VALUES(?,?,?,?,?,?)",
                   ("local-orphan-17", "alice", "local", None, "orphan-17", "local"))
    result = audit(storage, root, staging, verify_files=True)
    source, = result["sources"]
    receipt, = result["receipts"]
    assert source["code"] == "source_unlinked"
    assert source["origin_link_present"] is False
    assert receipt["staging_only"] is True
    assert receipt["physical_artifact_found"] is True
    assert receipt["staged_visible_in_library"] is True
    assert receipt["published_final_placement"] is False
    assert receipt["origin_link_present"] is False
    assert result["counts"]["unlinked_receipts_staged"] == 1
    assert result["counts"]["staged_visible_in_library"] == 1


def test_without_sha_verification_no_complete_local_claim(setup_db):
    storage, root, staging = setup_db
    insert_book(storage, root, staging, i=1, zone="final")
    result = audit(storage, root, staging, verify_files=False)
    assert result["sources"][0]["code"] == "published_unverified"
    assert result["counts"].get("complete_local", 0) == 0


def test_title_or_sha_only_is_never_exact_provenance(setup_db):
    storage, root, staging = setup_db
    insert_book(storage, root, staging, i=2, zone="final", origin=False)
    result = audit(storage, root, staging, verify_files=True)
    assert result["sources"][0]["code"] == "source_unlinked"
    assert result["sources"][0]["match_basis"] == "none"
    assert result["sources"][0]["ui_projection_linked"] is False


def test_missing_file_does_not_delete_or_infer_lost_right(setup_db):
    storage, root, staging = setup_db
    insert_book(storage, root, staging, i=1, zone="missing")
    result = audit(storage, root, staging, verify_files=True)
    assert result["sources"][0]["code"] == "missing_file"
    assert result["sources"][0]["origin_link_present"] is True
    assert result["sources"][0]["local_artifact_found"] is False


def test_symlink_to_other_profile_is_never_physical_proof(setup_db):
    storage, root, staging = setup_db
    target = insert_book(storage, root, staging, i=1, zone="final")
    elsewhere = root / "Bob"
    elsewhere.mkdir()
    wrong = elsewhere / "bad.epub"
    target.rename(wrong)
    target.symlink_to(wrong)
    result = audit(storage, root, staging, verify_files=True)
    assert result["counts"].get("complete_local", 0) == 0


def test_cross_account_rows_do_not_leak(setup_db):
    storage, root, staging = setup_db
    insert_book(storage, root, staging, i=1, zone="final")
    with storage._connect() as db:
        db.execute("INSERT INTO source_references VALUES(?,?,?,?,?,?,?,?,?,?)",
                   ("other", "bob", "numilog", "cred2", "accountB",
                    "edition-other", "right-other", "purchase", "active", 1))
        db.execute("INSERT INTO acquisition_ledger VALUES(?,?,?,?,?,?,?,?,?)",
                   ("other-secret-receipt", "bob", "numilog", "cred2",
                    "accountB", "failed", None, 1, "raw-private-token"))
    result = audit(storage, root, staging, verify_files=True)
    assert result["source_count"] == 1 and result["receipt_count"] == 1
    assert "other-secret" not in __import__("json").dumps(result)


def test_account_not_configured_fails_without_network(setup_db):
    storage, root, staging = setup_db
    with pytest.raises(ValueError, match="source_not_configured"):
        audit_account(storage, identity_id="alice", provider_id="numilog",
                      credential_id="cred2", account_id="accountB",
                      library_root=root, staging_roots=(staging,))


def test_disabled_account_fails_closed(setup_db):
    storage, root, staging = setup_db
    with storage._connect() as db:
        db.execute("UPDATE provider_connections SET enabled=0 WHERE connection_id='c1'")
    with pytest.raises(ValueError, match="source_disabled"):
        audit(storage, root, staging, verify_files=True)


def test_staging_without_supplied_root_is_not_declared_final(setup_db):
    storage, root, staging = setup_db
    insert_book(storage, root, staging, i=1, zone="staging")
    result = audit_account(storage, identity_id="alice", provider_id="numilog",
                           credential_id="cred1", account_id="accountA",
                           library_root=root, opaque_salt=b"0"*16,
                           verify_files=True)
    assert result["sources"][0]["code"] == "placement_unverified"
    assert result["coverage"]["staging"] == "unavailable"
