"""#3882: true indexed staging versus final placement, without assigning ownership."""
from __future__ import annotations

import hashlib
import json

from cifereca.core.acquisition_audit import inspect_exact_source
from cifereca.core.sync_report import audit_source_counts
from cifereca.storage import Storage
from types import SimpleNamespace


ACCOUNT = SimpleNamespace(
    identity_id="private-profile-A", provider_id="numilog",
    credential_id="private-credential-A", unique_id="account-A",
)


def _receipt(storage, *, artifact, key="receipt-one"):
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO acquisition_ledger("
            "acquisition_id,identity_id,provider_id,credential_id,"
            "account_id,item_id,status,artifact_id,metadata_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (key, ACCOUNT.identity_id, ACCOUNT.provider_id,
             ACCOUNT.credential_id, ACCOUNT.unique_id,
             "private-book-id", "canonical_receipt_staged",
             artifact, "{}", 1, 1),
        )


def _setup(tmp_path, *, is_staging=True, visible=True):
    storage = Storage(tmp_path / "library.sqlite")
    root = tmp_path / "EBooks"
    root.mkdir()
    if is_staging:
        path = tmp_path / "acquisition-work" / "private-profile-A" / "numilog" / "book.epub"
    else:
        path = root / "profile-A" / "numilog" / "book.epub"
    path.parent.mkdir(parents=True)
    data = b"synthetic-private-epub-not-a-production-book"
    path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    artifact = "sha256:" + sha
    storage.artifact_put({
        "artifact_id": artifact, "sha256": sha,
        "size": len(data), "media_format": "epub",
    })
    storage.placement_put({
        "placement_id": "placement1", "artifact_id": artifact,
        "path": str(path), "identity_id": ACCOUNT.identity_id,
        "provider_id": "numilog", "source_item_id": "book-id",
        "state": "present",
    })
    _receipt(storage, artifact=artifact)
    if visible:
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO catalog_entries("
                "catalog_entry_id,artifact_id,placement_id,title,identity_id,"
                "provider_id,local_state,metadata_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("catalog1", artifact, "placement1", "secret title",
                 ACCOUNT.identity_id, "local", "local", "{}", 1, 1),
            )
    return storage, root, path


def test_staged_book_local_card_without_origin_is_not_published(tmp_path):
    storage, root, path = _setup(tmp_path)
    try:
        result = inspect_exact_source(
            storage, ACCOUNT, library_root=root, verify_files=True,
        )
        staged = result["staging_evidence"]
        assert staged["coverage"] == "indexed_account_receipts_only"
        assert staged["staged_verified"] == 1
        assert staged["staged_visible_in_library"] == 1
        row, = staged["observations"]
        assert row["staging_sha_verified"] is True
        assert row["staged_visible_in_library"] is True
        assert row["origin_proven_by_receipt"] is False
        assert "private-profile" not in json.dumps(result)
        assert str(path) not in json.dumps(result)
        after = audit_source_counts(storage, ACCOUNT)
        assert after["staged_sha_verified_count"] == 1
        assert after["staged_visible_in_library_count"] == 1
        assert after["placement_reference_count"] == 0  # no exact OriginLink
    finally:
        storage.close()


def test_final_file_is_not_a_staging_publication_claim(tmp_path):
    storage, root, path = _setup(tmp_path, is_staging=False)
    try:
        result = inspect_exact_source(
            storage, ACCOUNT, library_root=root, verify_files=True,
        )
        assert result["staging_evidence"]["staged_verified"] == 0
        assert result["staging_evidence"]["staged_visible_in_library"] == 0
    finally:
        storage.close()


def test_no_file_hash_requested_is_not_verified(tmp_path):
    storage, root, _path = _setup(tmp_path)
    try:
        result = inspect_exact_source(storage, ACCOUNT)
        assert result["staging_evidence"]["staged_verified"] == 0
        assert result["staging_evidence"]["observations"][0]["staging_state"] == "indexed_staging_unverified"
    finally:
        storage.close()


def test_missing_or_symlink_staging_never_claims_presence(tmp_path):
    storage, root, path = _setup(tmp_path)
    try:
        path.unlink()
        missing = inspect_exact_source(storage, ACCOUNT, library_root=root, verify_files=True)
        assert missing["staging_evidence"]["staged_verified"] == 0
        outside = tmp_path / "private"
        outside.write_bytes(b"synthetic-private-epub-not-a-production-book")
        path.symlink_to(outside)
        symlink = inspect_exact_source(storage, ACCOUNT, library_root=root, verify_files=True)
        assert symlink["staging_evidence"]["staged_verified"] == 0
    finally:
        storage.close()


def test_cross_account_ledger_cannot_affect_receipt_counts(tmp_path):
    storage, root, _path = _setup(tmp_path)
    try:
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,identity_id,provider_id,credential_id,"
                "account_id,item_id,status,metadata_json,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("unrelated", "private-other", "numilog", "other-credential",
                 "other-account", "other-item", "canonical_receipt_staged", "{}", 1, 1),
            )
        data = inspect_exact_source(storage, ACCOUNT, library_root=root, verify_files=True)
        assert data["staging_evidence"]["staged_verified"] == 1
        assert len(data["staging_evidence"]["observations"]) == 1
        assert "unrelated" not in json.dumps(data)
    finally:
        storage.close()
