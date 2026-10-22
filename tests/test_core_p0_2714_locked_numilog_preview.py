"""#2714: fresh Numilog SQLite proof preview is strictly non-mutating."""
import hashlib
import json
import sqlite3

import pytest

from cifereca.core.library_inventory import scan_identity_roots
from cifereca.core.numilog_link_preview import preview_numilog_link
from cifereca.storage import Storage
from tests.test_library_reconciliation import epub, filesystem


@pytest.fixture
def evidence(tmp_path):
    storage = Storage(tmp_path / "evidence.sqlite")
    root = tmp_path / "alice"
    root.mkdir()
    book = root / "test.epub"
    epub(book)
    observation, = scan_identity_roots({"alice": (root,)}).files
    right = "right-numilog"
    artifact = "sha256:" + observation.sha256
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO provider_connections "
            "(connection_id,identity_id,provider_id,credential_id,label,enabled,created_at,updated_at) "
            "VALUES (?,?,?,?,?,1,1,1)",
            ("conn", "alice", "numilog", "cred", "Compte fictif"),
        )
        db.execute(
            "INSERT INTO entitlements "
            "(entitlement_id,identity_id,provider_id,credential_id,account_id,"
            "item_id,state,entitlement_type,metadata_json,discovered_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (right, "alice", "numilog", "cred", "account",
             "edition", "active", "owned",
             json.dumps({"source": "numilog.authenticated_library"}), 1, 1),
        )
        db.execute(
            "INSERT INTO artifacts (artifact_id,sha256,size,media_format,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (artifact, observation.sha256, observation.size, "epub", 1, 1),
        )
        db.execute(
            "INSERT INTO acquisition_ledger "
            "(acquisition_id,entitlement_id,identity_id,provider_id,credential_id,"
            "account_id,item_id,status,artifact_id,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("receipt", right, "alice", "numilog", "cred", "account",
             "edition", "complete", artifact, 1, 1),
        )
    yield storage, root, observation
    storage.close()


def preview(evidence, **overrides):
    storage, root, file = evidence
    args = {"identity_id": "alice", "credential_id": "cred",
            "account_id": "account", "item_id": "edition", "roots": (root,)}
    args.update(overrides)
    return preview_numilog_link(storage, file, **args)


def test_valid_scoped_receipt_is_reviewable_but_never_authorizes_write(evidence, tmp_path):
    before = filesystem(tmp_path)
    result = preview(evidence)
    assert result.status == "reviewable"
    assert result.reason == "fresh_proofs_require_confirmation"
    assert result.read_only and not result.attachment_authorized
    assert result.requires_explicit_confirmation
    assert filesystem(tmp_path) == before


@pytest.mark.parametrize("update,reason", [
    ("UPDATE entitlements SET state='revoked'", "right_not_attested"),
    ("UPDATE acquisition_ledger SET account_id='foreign'", "acquisition_conflict"),
    ("UPDATE acquisition_ledger SET status='pending'", "acquisition_conflict"),
    ("UPDATE artifacts SET size=1", "artifact_mismatch"),
    ("INSERT INTO placements (placement_id,path,identity_id,provider_id,source_item_id,state,created_at,updated_at) "
     "VALUES ('other','/tmp/conflict','alice','numilog','edition','present',1,1)", "placement_conflict"),
])
def test_mutated_evidence_fails_closed(evidence, update, reason):
    storage, *_ = evidence
    with storage._lock, storage._connect() as db:
        db.execute(update)
    result = preview(evidence)
    assert result.status == "blocked"
    assert result.reason == reason
    assert not result.attachment_authorized


def test_foreign_identity_wrong_account_and_cancelled_are_denied(evidence):
    assert preview(evidence, identity_id="bob").reason == "invalid_scope"
    assert preview(evidence, account_id="foreign").reason == "right_not_attested"
    assert preview(evidence, cancelled=lambda: True).reason == "cancelled"


def test_changed_file_and_symlink_are_denied(evidence):
    storage, root, observation = evidence
    target = root / "test.epub"
    target.write_bytes(target.read_bytes() + b"changed")
    assert preview(evidence).reason == "file_file_changed"
    assert not preview(evidence).attachment_authorized


def test_no_sqlite_writes_even_when_client_preview_is_repeated(evidence, tmp_path):
    before = filesystem(tmp_path)
    for _ in range(3):
        assert preview(evidence).status == "reviewable"
    assert filesystem(tmp_path) == before


def test_second_same_edition_entitlement_is_ambiguous(evidence):
    storage, *_ = evidence
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO entitlements (entitlement_id,identity_id,provider_id,"
            "credential_id,account_id,item_id,state,entitlement_type,metadata_json,discovered_at,updated_at)"
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("duplicate", "alice", "numilog", "cred", "another_account",
             "edition", "active", "owned",
             json.dumps({"source": "numilog.authenticated_library"}), 1, 1),
        )
    result = preview(evidence)
    assert result.reason == "right_ambiguous_or_missing"
    assert not result.attachment_authorized


def test_second_acquisition_for_same_edition_is_ambiguous(evidence):
    storage, *_ = evidence
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO acquisition_ledger "
            "(acquisition_id,entitlement_id,identity_id,provider_id,credential_id,"
            "account_id,item_id,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("other-receipt", "other-right", "alice", "numilog", "cred",
             "account", "edition", "complete", 1, 1),
        )
    result = preview(evidence)
    assert result.reason == "acquisition_ambiguous_or_missing"
    assert not result.attachment_authorized


def test_disabled_connection_cannot_count_as_authenticated_purchase(evidence):
    storage, *_ = evidence
    with storage._lock, storage._connect() as db:
        db.execute("UPDATE provider_connections SET enabled=0")
    assert preview(evidence).reason == "source_unavailable"


def test_duplicated_connection_across_identities_is_not_a_scoped_binding(evidence):
    storage, *_ = evidence
    with storage._lock, storage._connect() as db:
        db.execute(
            "INSERT INTO provider_connections "
            "(connection_id,identity_id,provider_id,credential_id,label,enabled,created_at,updated_at) "
            "VALUES (?,?,?,?,?,1,1,1)",
            ("foreign-connection", "bob", "numilog", "cred", "Foreign synthetic"),
        )
    result = preview(evidence)
    assert result.reason == "source_unavailable"
    assert result.status == "blocked" and not result.attachment_authorized
