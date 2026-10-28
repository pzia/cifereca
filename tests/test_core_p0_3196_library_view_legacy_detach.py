"""#3196: the Library UI never reads unscoped v0.7 backup_items.

All observations are local SQLite and synthetic; no provider, login or GET.
"""
from __future__ import annotations

import json

from tests.test_core_1337_kobo_personal_manifest_rights import _service


def _deny_backup(*args, **kwargs):
    raise AssertionError("Library view cannot read unscoped backup_items")


def test_bob_entitlement_never_uses_alices_backup_title_cover_or_format(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        service.storage.entitlement_put({
            "provider_id": "kobo", "credential_id": "shared-card",
            "identity_id": "bob", "account_id": "bob-buyer",
            "item_id": "same-edition", "entitlement_type": "owned",
            "state": "active", "metadata": {
                "title": "Bob's verified purchase",
                "author": "Bob's author", "media_format": "pdf",
                "cover_url": "https://covers.example/bob.jpg",
                "source": "attested_personal_manifest",
            },
        })
        # The old backup PK contains neither identity nor account.
        # Deliberately install a stale Alice row sharing both item and card.
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "INSERT INTO backup_items(provider_id,credential_id,item_id,"
                "title,author,format,status,updated_at,identity_id,account_id,cover_url)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("kobo", "shared-card", "same-edition",
                 "ALICE_PRIVATE_TITLE", "ALICE_PRIVATE_AUTHOR", "epub",
                 "complete", 1, "alice", "alice-buyer",
                 "https://private.invalid/ALICE_COVER"),
            )
        monkeypatch.setattr(service.storage, "backup_list", _deny_backup)
        rows = service.library_entries(identity_id="bob", scan=False)
        assert len(rows) == 1
        row = rows[0]
        assert row["title"] == "Bob's verified purchase"
        assert row["author"] == "Bob's author"
        assert row["media_format"] == "pdf"
        assert row["thumbnail_url"] == "https://covers.example/bob.jpg"
        assert "ALICE_PRIVATE" not in json.dumps(row)
        assert service.library_entries(identity_id="alice", scan=False) == []
    finally:
        service.close()


def test_legacy_writer_metadata_is_visible_via_entitlement_only(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        service.storage.backup_put({
            "provider_id": "kobo", "credential_id": "legacy-a",
            "identity_id": "alice", "account_id": "alice-buyer",
            "item_id": "old-edition", "title": "Historical owned",
            "author": "Historical author", "format": "EPUB",
            "cover_url": "https://covers.example/historical.jpg",
            "status": "pending", "entitlement_type": "owned",
        })
        assert len(service.storage.entitlements(provider_id="kobo")) == 1
        monkeypatch.setattr(service.storage, "backup_list", _deny_backup)
        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 1
        assert rows[0]["title"] == "Historical owned"
        assert rows[0]["author"] == "Historical author"
        assert rows[0]["media_format"] == "epub"
        assert rows[0]["thumbnail_url"] == "https://covers.example/historical.jpg"
        assert rows[0]["local"] is False
    finally:
        service.close()


def test_unmirrored_backup_row_is_not_a_library_right(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        with service.storage._lock, service.storage._connect() as db:
            db.execute(
                "INSERT INTO backup_items(provider_id,credential_id,item_id,"
                "title,status,updated_at,identity_id,account_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("kobo", "orphan", "orphaned", "PRIVATE_ORPHAN",
                 "complete", 1, "alice", "old-account"),
            )
        monkeypatch.setattr(service.storage, "backup_list", _deny_backup)
        assert service.library_entries(identity_id="alice", scan=False) == []
        assert service.library_entries(identity_id="bob", scan=False) == []
    finally:
        service.close()
