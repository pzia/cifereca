"""#4858 W2: an explicitly foreign 0.7 receipt is not transferable B.

Old Kobo archive keys omit Core identity and borrower. An explicitly
different owner must not be adopted. Unscoped 0.7 receipts retain their
bounded legacy reconciliation until attested migration. No provider I/O.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.core import kobo_backup
from tests.test_kobo_backup_guard import _manifest_row, _service


@pytest.mark.parametrize(("old_identity", "old_account"), (
    ("bob", "account"),
    ("alice", "other-borrower"),
    ("bob", "other-borrower"),
    ("alice", " "),
))
def test_manifest_refresh_refuses_explicit_foreign_archive_before_any_write(
    tmp_path, monkeypatch, old_identity, old_account,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    original = {
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": old_identity, "account_id": old_account,
        "item_id": "book-1", "revision_id": "old-revision",
        "title": "Private historic title", "status": "complete",
        "sha256": "a" * 64, "size": 42,
        "local_path": "/private/foreign/ebook.epub",
    }
    calls = []
    monkeypatch.setattr(
        service.storage, "backup_get",
        lambda *_a, **_kw: calls.append("backup_get") or dict(original),
    )
    monkeypatch.setattr(
        service.storage, "backup_put",
        lambda *_a, **_kw: pytest.fail("explicit foreign archive must not be relabelled"),
    )
    monkeypatch.setattr(
        session, "cover_url",
        lambda *_a: pytest.fail("no provider metadata access for explicit foreign owner"),
    )
    try:
        with pytest.raises(
            kobo_backup._KoboLegacySessionChanged,
            match="kobo_legacy_archive_owner_changed",
        ):
            kobo_backup._refresh_manifest_record(
                service, session, SimpleNamespace(identity_id="alice"),
                _manifest_row(), "book-1", {"metadata_refreshed": 0},
                source_current=lambda: True,
            )
        assert calls == ["backup_get"]
    finally:
        service.close()


@pytest.mark.parametrize("account", ("account", ""))
def test_manifest_refresh_keeps_exact_current_borrower_compatibility(
    tmp_path, monkeypatch, account,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    session.accounts[0].unique_id = account
    old = {
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "alice", "account_id": account,
        "item_id": "book-1", "revision_id": "old-revision",
        "title": "Old title", "status": "pending",
        "sha256": None, "size": None, "local_path": None,
    }
    persisted = []
    monkeypatch.setattr(service.storage, "backup_get", lambda *_a, **_kw: dict(old))
    monkeypatch.setattr(service.storage, "backup_put", lambda row: persisted.append(dict(row)))
    try:
        item = kobo_backup._refresh_manifest_record(
            service, session, SimpleNamespace(identity_id="alice"),
            _manifest_row(), "book-1", {"metadata_refreshed": 0},
            source_current=lambda: True,
        )
        assert len(persisted) == 1
        assert persisted[0]["identity_id"] == "alice"
        assert persisted[0]["account_id"] == account
        assert item.account_id == account
    finally:
        service.close()


def test_foreign_archive_stays_byte_for_byte_unchanged_in_sqlite(tmp_path):
    """Even a live source cannot take over an existing foreign W2 receipt."""
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    old = {
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "bob", "account_id": "foreign",
        "item_id": "book-1", "revision_id": "old-revision",
        "title": "Private historic title", "status": "complete",
        "sha256": "a" * 64, "size": 42,
        "local_path": "/private/foreign/ebook.epub",
    }
    try:
        service.storage.backup_put(old)
        before = service.storage.backup_get("kobo", "vault-1", "book-1")
        with pytest.raises(
            kobo_backup._KoboLegacySessionChanged,
            match="kobo_legacy_archive_owner_changed",
        ):
            kobo_backup._refresh_manifest_record(
                service, session, SimpleNamespace(identity_id="alice"),
                _manifest_row(), "book-1", {"metadata_refreshed": 0},
                source_current=lambda: True,
            )
        assert service.storage.backup_get("kobo", "vault-1", "book-1") == before
    finally:
        service.close()
