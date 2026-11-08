"""P0 #4858 W2: raw Kobo claims cannot be laundered into an owned 0.7 right."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_kobo_backup_guard import _manifest_row, _service, _write_epub


@pytest.mark.parametrize("extra", [
    {"account_id": "foreign-borrower"},
    {"account_id": None},
    {"credential_id": "other-vault"},
    {"provider_id": "other-provider"},
    {"loan_id": "loan-1"},
    {"expires_at": "2026-11-15T00:00:00Z"},
    {"entitlement_type": "loan"},
    {"entitlement_type": False},
    {"product_id": "book-1", "id": "another-edition"},
])
def test_foreign_or_loan_claim_never_mirrors_or_dispatches_get(
    tmp_path: Path, monkeypatch, extra,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    session._manifest = [{**_manifest_row(), **extra}]
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("foreign/loan row must not GET"),
    )
    try:
        result = service.sync_kobo_backup()
        assert result["attempted"] == result["downloaded"] == 0
        assert result["malformed_manifest_items"] >= 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1") is None
        assert not [
            row for row in service.storage.entitlements(provider_id="kobo")
            if row.get("credential_id") == "vault-1"
            and row.get("item_id") == "book-1"
        ]
    finally:
        service.close()


def test_foreign_manifest_cannot_relabel_existing_archive(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    row = {
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "alice", "account_id": "account",
        "item_id": "book-1", "revision_id": "rev-1",
        "title": "Prior archive", "status": "complete",
    }
    service.storage.backup_put(row)
    before = service.storage.backup_get("kobo", "vault-1", "book-1")
    session = service._sessions[service._session_key("kobo", "vault-1")]
    session._manifest = [{**_manifest_row(), "account_id": "foreign-borrower",
                          "title": "Foreign new title"}]
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("invalid manifest cannot dispatch GET"),
    )
    try:
        result = service.sync_kobo_backup()
        assert result["attempted"] == 0
        assert result["malformed_manifest_items"] >= 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1") == before
    finally:
        service.close()


def test_exact_legacy_purchase_claim_keeps_first_delivery_contract(
    tmp_path: Path, monkeypatch,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    session._manifest = [{**_manifest_row(),
                          "provider_id": "kobo", "credential_id": "vault-1",
                          "account_id": "account", "entitlement_type": "owned"}]
    calls = []

    def acquire(_item, target):
        calls.append("GET")
        path = Path(target) / "exact-book.epub"
        _write_epub(path)
        return SimpleNamespace(path=path, account_id="account", metadata={})

    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        result = service.sync_kobo_backup()
        assert calls == ["GET"]
        assert result["downloaded"] == 1
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row["status"] == "complete"
    finally:
        service.close()


@pytest.mark.parametrize("extra", [
    {"product_id": 123},
    {"product_id": True},
    {"product_id": " book-1"},
    {"product_id": "book-1 "},
    {"product_id": "book-1\\n"},
    {"product_id": "book-1\\x00"},
    {"product_id": "x" * 1025},
    {"product_id": "book-1", "id": 123},
    {"product_id": "book-1", "id": "book-1 "},
    {"product_id": "book-1", "id": "book-1\\r"},
])
def test_malformed_raw_edition_key_never_mints_right_or_get(
    tmp_path: Path, monkeypatch, extra,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    session._manifest = [{**_manifest_row(), **extra}]
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("invalid edition key cannot GET"),
    )
    try:
        result = service.sync_kobo_backup()
        assert result["attempted"] == result["downloaded"] == 0
        assert result["malformed_manifest_items"] >= 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1") is None
        assert not service.storage.entitlements(provider_id="kobo")
    finally:
        service.close()


def test_raw_exact_dual_edition_aliases_remain_compatible(
    tmp_path: Path, monkeypatch,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    session._manifest = [{**_manifest_row(), "id": "book-1"}]
    calls = []

    def acquire(_item, target):
        calls.append("GET")
        path = Path(target) / "dual-alias.epub"
        _write_epub(path)
        return SimpleNamespace(path=path, account_id="account", metadata={})

    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        result = service.sync_kobo_backup()
        assert result["downloaded"] == 1
        assert calls == ["GET"]
        assert service.storage.backup_get("kobo", "vault-1", "book-1")["status"] == "complete"
    finally:
        service.close()
