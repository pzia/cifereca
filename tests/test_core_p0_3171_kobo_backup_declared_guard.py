"""P0 #3171: an exact-scope Kobo declaration prevents a second supplier GET.

The declaration is not an acquired Kobo receipt; a revoked, mismatched or
unreadable declaration never becomes a successful backup.
"""
from cifereca.core.library_declared_links import (
    _PREFIX, declare_local_link, has_active_declared_link,
)
from cifereca.core.library_inventory import scan_identity_roots
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_guard import _service
from tests.test_library_reconciliation import epub


def _declared_kobo(tmp_path, monkeypatch):
    service = _service(tmp_path)
    session = service._sessions[service._session_key("kobo", "vault-1")]
    account, = session.accounts
    service.accounts = [account]
    service.current_identity = service.identity_by_id("alice")
    service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    service.storage.entitlement_put({
        "entitlement_id": "kobo:book-1", "identity_id": "alice",
        "provider_id": "kobo", "credential_id": "vault-1",
        "account_id": "account", "item_id": "book-1",
        "entitlement_type": "owned", "state": "active",
        "metadata": {"title": "Book One"},
    })
    book = tmp_path / "EBooks" / "alice" / "local" / "not-a-kobo-filename.epub"
    book.parent.mkdir(parents=True)
    epub(book, title="Book One")
    observed, = scan_identity_roots({"alice": (book.parents[1],)}).files
    scope = dict(identity_id="alice", provider_id="kobo", credential_id="vault-1",
                 account_id="account", item_id="book-1")
    from cifereca.core import library_declared_links
    monkeypatch.setattr(library_declared_links, "acquisition_lock_file",
                        lambda: tmp_path / "declaration.lock")
    preview = declare_local_link(service, observation=observed, **scope)
    result = declare_local_link(service, observation=observed,
                                confirmation=preview.plan_token, **scope)
    assert result.status == "declared"
    monkeypatch.setattr(service, "catalog_scan", lambda *a, **k: {"scanned": 1})
    return service, book, scope, result.relation_id


def _no_download(service, monkeypatch):
    calls = []
    def deny(*args, **kwargs):
        calls.append(1)
        raise ProviderConfigurationError("synthetic-no-file")
    monkeypatch.setattr(service, "_execute_kobo_acquisition", deny)
    return calls


def test_active_declaration_blocks_even_with_unlinked_override_and_second_sync(tmp_path, monkeypatch):
    service, book, scope, relation = _declared_kobo(tmp_path, monkeypatch)
    calls = _no_download(service, monkeypatch)
    original = book.read_bytes()
    try:
        for _ in range(2):
            summary = service.sync_kobo_backup()
            row = service.storage.backup_get("kobo", "vault-1", "book-1")
            assert summary["attempted"] == summary["downloaded"] == 0
            assert summary["attention_required"] == 0
            assert summary["already_local_declared"] == 1
            assert summary["sources"][0]["already_local_declared"] == 1
            assert row["status"] == "attention_required"
            assert row["attention_code"] == "declared_link_recorded"
            assert book.read_bytes() == original
            assert has_active_declared_link(service.storage, **scope)
            assert calls == []
            # backup_put mirrors an attention_required row into the legacy
            # acquisition ledger. This is NOT a download, receipt or placement.
            ledger = service.storage.acquisitions(provider_id="kobo")
            assert all(entry["status"] == "attention_required"
                       and entry["attempt_count"] == 0
                       and entry["artifact_id"] is None for entry in ledger)
            assert service.storage.placements() == []
    finally:
        service.close()


def test_missing_declared_file_requires_review_without_supplier_io(tmp_path, monkeypatch):
    service, book, scope, _ = _declared_kobo(tmp_path, monkeypatch)
    calls = _no_download(service, monkeypatch)
    book.unlink()
    try:
        result = service.sync_kobo_backup()
        assert result["attempted"] == 0 and result["attention_required"] == 1
        assert result["already_local_declared"] == 0
        assert service.storage.backup_get("kobo", "vault-1", "book-1")[
            "attention_code"] == "declared_link_recorded"
        assert calls == [] and not book.exists()
    finally:
        service.close()


def test_revoked_declaration_no_longer_blocks_kobo(tmp_path, monkeypatch):
    service, book, scope, relation = _declared_kobo(tmp_path, monkeypatch)
    calls = _no_download(service, monkeypatch)
    try:
        observed, = scan_identity_roots({"alice": (book.parents[1],)}).files
        preview = declare_local_link(service, observation=observed, revoke=relation, **scope)
        revoke = declare_local_link(service, observation=observed, revoke=relation,
                                    confirmation=preview.plan_token, **scope)
        assert revoke.status == "revoked"
        assert not has_active_declared_link(service.storage, **scope)
        summary = service.sync_kobo_backup()
        assert summary["attempted"] == len(calls) == 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1")[
            "attention_code"] != "declared_link_recorded"
        assert book.is_file()
    finally:
        service.close()


def test_another_account_cannot_inherit_statement(tmp_path, monkeypatch):
    service, book, scope, _ = _declared_kobo(tmp_path, monkeypatch)
    calls = _no_download(service, monkeypatch)
    try:
        account, = service.accounts
        account.unique_id = "different-borrower"
        assert not has_active_declared_link(service.storage, **{**scope, "account_id": "different-borrower"})
        summary = service.sync_kobo_backup()
        assert summary["attempted"] == len(calls) == 1
        assert service.storage.backup_get("kobo", "vault-1", "book-1")[
            "attention_code"] != "declared_link_recorded"
        assert book.is_file()
    finally:
        service.close()


def test_corrupt_local_declaration_namespace_fails_closed(tmp_path, monkeypatch):
    service, book, scope, _ = _declared_kobo(tmp_path, monkeypatch)
    calls = _no_download(service, monkeypatch)
    try:
        service.storage.metadata_set(_PREFIX + "f" * 32, "{malformed-secret")
        summary = service.sync_kobo_backup()
        assert summary["attempted"] == 0 and summary["attention_required"] == 1
        assert summary["already_local_declared"] == 0
        row = service.storage.backup_get("kobo", "vault-1", "book-1")
        assert row["attention_code"] == "declared_links_unavailable"
        assert "malformed-secret" not in str(summary)
        assert calls == [] and book.is_file()
    finally:
        service.close()
