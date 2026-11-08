"""P0 #4858 W2: conflicting manifest aliases quarantine all implicated rows.

Real Core/SQLite/EPUB fixtures. No remote Kobo API and no ADEPT runtime.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_kobo_backup_guard import _manifest_row, _service, _write_epub


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("ambiguous", [
    {"product_id": "other-edition", "id": "book-1"},
    {"product_id": "other-edition", "id": " book-1 "},
    {"product_id": "other-edition", "id": "book-1\n"},
])
def test_conflicting_secondary_alias_quarantines_entire_edition(
    tmp_path: Path, monkeypatch, reverse: bool, ambiguous: dict,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    rows = [{**_manifest_row(), **ambiguous}, _manifest_row()]
    session._manifest = list(reversed(rows)) if reverse else rows
    dispatched = []
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: dispatched.append("GET"),
    )
    try:
        result = service.sync_kobo_backup()
        assert result["attempted"] == result["downloaded"] == 0
        assert result["malformed_manifest_items"] == 2
        assert result["duplicate_manifest_items"] == 2
        assert result["status"] == "partial"
        assert dispatched == []
        assert service.storage.backup_get("kobo", "vault-1", "book-1") is None
        assert service.storage.backup_get("kobo", "vault-1", "other-edition") is None
        assert not service.storage.entitlements(provider_id="kobo")
        replay = service.sync_kobo_backup()
        assert replay["attempted"] == 0
        assert dispatched == []
    finally:
        service.close()


def test_malformed_alias_sibling_does_not_block_unrelated_exact_purchase(
    tmp_path: Path, monkeypatch,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    session._manifest = [
        _manifest_row(),
        {**_manifest_row(), "product_id": "other-edition", "id": "book-1"},
        {**_manifest_row(), "product_id": "safe-edition", "revision_id": "rev-safe"},
    ]
    calls = []

    def acquire(item, target):
        calls.append(item.record_id)
        assert item.record_id == "safe-edition"
        path = Path(target) / "safe-edition.epub"
        _write_epub(path)
        return SimpleNamespace(path=path, account_id="account", metadata={})

    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        result = service.sync_kobo_backup()
        assert calls == ["safe-edition"]
        assert result["downloaded"] == result["attempted"] == 1
        assert result["duplicate_manifest_items"] == 2
        assert result["malformed_manifest_items"] == 2
        assert service.storage.backup_get("kobo", "vault-1", "book-1") is None
        assert service.storage.backup_get("kobo", "vault-1", "safe-edition")["status"] == "complete"
    finally:
        service.close()


def test_colliding_alias_cannot_rewrite_prior_complete_archive(
    tmp_path: Path, monkeypatch,
):
    service = _service(tmp_path)
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": "alice", "account_id": "account",
        "item_id": "book-1", "revision_id": "rev-1",
        "title": "Prior purchase", "status": "complete",
    })
    before = service.storage.backup_get("kobo", "vault-1", "book-1")
    session = service._sessions[service._session_key("kobo", "vault-1")]
    session._manifest = [
        _manifest_row(),
        {**_manifest_row(), "product_id": "other-edition", "id": "book-1"},
    ]
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("ambiguous archive must never GET"),
    )
    try:
        result = service.sync_kobo_backup()
        assert result["attempted"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "book-1") == before
    finally:
        service.close()


def test_same_row_identical_dual_alias_not_counted_as_duplicate(
    tmp_path: Path, monkeypatch,
):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    session._manifest = [{**_manifest_row(), "id": "book-1"}]
    calls = []
    def acquire(_item, target):
        calls.append("GET")
        path = Path(target) / "identical-alias.epub"
        _write_epub(path)
        return SimpleNamespace(path=path, account_id="account", metadata={})
    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        result = service.sync_kobo_backup()
        assert result["duplicate_manifest_items"] == 0
        assert calls == ["GET"]
        assert result["downloaded"] == 1
    finally:
        service.close()
