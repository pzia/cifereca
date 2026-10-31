"""#3882: no-network, exact-scope acquisition audit with physical SHA evidence."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from cifereca.core.acquisition_audit import inspect_exact_source
from cifereca.storage import Storage


ACCOUNT = SimpleNamespace(
    identity_id="PRIVATE_PROFILE_A", provider_id="numilog",
    credential_id="PRIVATE_CARD_A", unique_id="PRIVATE_BUYER_A",
)
OTHER = SimpleNamespace(
    identity_id="PRIVATE_PROFILE_B", provider_id="numilog",
    credential_id="PRIVATE_CARD_B", unique_id="PRIVATE_BUYER_B",
)


def _ref(storage, account, reference):
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO source_references("
            "source_reference_id,identity_id,provider_id,credential_id,account_id,"
            "branch_id,edition_id,right_id,reference_kind,state,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (reference, account.identity_id, account.provider_id,
             account.credential_id, account.unique_id,
             "purchases", reference, "right", "purchase", "active", 1, 1),
        )


def test_exact_source_and_staged_receipt_are_independent(tmp_path):
    storage = Storage(tmp_path / "ledger.sqlite")
    try:
        _ref(storage, ACCOUNT, "PRIVATE_RIGHT_A")
        _ref(storage, OTHER, "PRIVATE_RIGHT_B")
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO acquisition_ledger("
                "acquisition_id,identity_id,provider_id,credential_id,account_id,"
                "item_id,status,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("PRIVATE_RECEIPT_A", ACCOUNT.identity_id, ACCOUNT.provider_id,
                 ACCOUNT.credential_id, ACCOUNT.unique_id, "PRIVATE_EDITION",
                 "canonical_receipt_staged", '{"path":"/private/secret"}', 1, 1),
            )
        result = inspect_exact_source(storage, ACCOUNT)
        assert len(result["rights"]) == 1
        assert len(result["receipts"]) == 1
        assert result["rights"][0]["classification"] == "source_unlinked"
        assert result["receipts"][0]["classification"] == "staged_unpublished"
        text = json.dumps(result)
        for private in ("PRIVATE_RIGHT_A", "PRIVATE_RIGHT_B", "PRIVATE_RECEIPT_A",
                        "PRIVATE_PROFILE_A", "PRIVATE_CARD_A", "PRIVATE_BUYER_A",
                        "/private/secret"):
            assert private not in text
    finally:
        storage.close()


def test_verified_source_placement_and_missing_file(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "ledger.sqlite")
    root = tmp_path / "books"
    directory = root / "Profile-A"
    directory.mkdir(parents=True)
    path = directory / "book.epub"
    payload = b"synthetic-book-content"
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    artifact_id = "sha256:" + digest
    monkeypatch.setattr(
        Storage, "identities",
        lambda self: [{"identity_id": ACCOUNT.identity_id,
                       "directory_name": "Profile-A"}],
    )
    try:
        _ref(storage, ACCOUNT, "PRIVATE_RIGHT_A")
        storage.artifact_put({
            "artifact_id": artifact_id, "sha256": digest,
            "size": len(payload), "media_format": "epub",
        })
        storage.placement_put({
            "placement_id": "p-a", "artifact_id": artifact_id, "path": str(path),
            "identity_id": ACCOUNT.identity_id, "provider_id": ACCOUNT.provider_id,
            "source_item_id": "edition-a", "state": "present",
        })
        with storage._lock, storage._connect() as db:
            db.execute(
                "INSERT INTO origin_links("
                "origin_link_id,origin_kind,identity_id,artifact_id,"
                "source_reference_id,evidence_ref,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("PRIVATE_ORIGIN_A", "provider", ACCOUNT.identity_id,
                 artifact_id, "PRIVATE_RIGHT_A", "PRIVATE_RECEIPT", 1, 1),
            )
        before = inspect_exact_source(
            storage, ACCOUNT, library_root=root, verify_files=True,
        )
        assert before["rights"][0]["classification"] == "complete_local"
        assert before["rights"][0]["file_verification"] == "verified_sha256"
        path.unlink()
        missing = inspect_exact_source(
            storage, ACCOUNT, library_root=root, verify_files=True,
        )
        assert missing["rights"][0]["classification"] == "missing_file"
        assert missing["rights"][0]["file_verification"] == "missing_file"
        assert "p-a" not in json.dumps(missing)
    finally:
        storage.close()


def test_symlink_outside_profile_is_not_verified(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "ledger.sqlite")
    root = tmp_path / "books"
    (root / "Profile-A").mkdir(parents=True)
    outside = tmp_path / "private-secret.epub"
    outside.write_bytes(b"secret-book")
    link = root / "Profile-A" / "symlink.epub"
    link.symlink_to(outside)
    digest = hashlib.sha256(outside.read_bytes()).hexdigest()
    try:
        from cifereca.core.acquisition_audit import _file_proof
        assert _file_proof(
            root=root, directory="Profile-A", path=str(link),
            artifact_id="sha256:" + digest,
        ) == "invalid_placement"
    finally:
        storage.close()
