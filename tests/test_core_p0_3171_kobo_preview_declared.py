"""P0 #3171: a consent preview cannot propose an already declared Kobo book."""
from cifereca.core.library_declared_links import (
    _PREFIX, declare_local_link, has_active_declared_link,
)
from cifereca.core.library_inventory import scan_identity_roots
from tests.test_core_p0_3171_kobo_backup_declared_guard import _declared_kobo


def _preview(service):
    return service.kobo_backup_preview("vault-1", refresh_manifest=True)


def test_exact_active_declaration_blocks_preview_before_provider_content_io(tmp_path, monkeypatch):
    service, book, scope, _ = _declared_kobo(tmp_path, monkeypatch)
    original = book.read_bytes()
    try:
        for _ in range(2):
            view = _preview(service)
            assert view["manifest_refreshed"]
            assert view["counts"]["attention_required"] == 1
            assert view["counts"]["potentially_missing"] == 0
            assert view["blocking_items"] == 1
            assert view["acquisition_authorized"] is False
            assert view["examples"][0]["reason"] == "attention_required"
            assert book.read_bytes() == original
            assert has_active_declared_link(service.storage, **scope)
    finally:
        service.close()


def test_revoked_declaration_reopens_preview_even_after_attention_checkpoint(tmp_path, monkeypatch):
    service, book, scope, relation = _declared_kobo(tmp_path, monkeypatch)
    try:
        blocked = service.sync_kobo_backup()
        assert blocked["attempted"] == 0
        assert blocked["already_local_declared"] == 1
        # The scoped checkpoint captures the verified presence. Creating a
        # compatibility backup would fabricate a second entitlement and
        # poison the next verification, so no backup/receipt is persisted.
        assert service.storage.backup_get("kobo", "vault-1", "book-1") is None
        assert service.storage.acquisitions(provider_id="kobo") == []
        observed, = scan_identity_roots({"alice": (book.parents[1],)}).files
        undo = declare_local_link(service, observation=observed, revoke=relation, **scope)
        assert undo.status == "revoke_preview"
        assert declare_local_link(
            service, observation=observed, revoke=relation,
            confirmation=undo.plan_token, **scope,
        ).status == "revoked"
        view = _preview(service)
        assert view["counts"]["attention_required"] == 0
        assert view["counts"]["potentially_missing"] == 1
        assert view["blocking_items"] == 0
        assert view["acquisition_authorized"] is False  # advisory only
        assert book.is_file()
    finally:
        service.close()


def test_other_borrower_never_inherits_declaration_preview(tmp_path, monkeypatch):
    service, book, _scope, _ = _declared_kobo(tmp_path, monkeypatch)
    try:
        service.accounts[0].unique_id = "another-borrower"
        view = _preview(service)
        assert view["counts"]["attention_required"] == 0
        assert view["counts"]["potentially_missing"] == 1
        assert view["blocking_items"] == 0
        assert book.is_file()
    finally:
        service.close()


def test_corrupt_declaration_store_blocks_preview_without_user_data(tmp_path, monkeypatch):
    service, book, _scope, _ = _declared_kobo(tmp_path, monkeypatch)
    try:
        service.storage.metadata_set(_PREFIX + "e" * 32, "{secret malformed")
        view = _preview(service)
        assert view["counts"]["attention_required"] == 1
        assert view["counts"]["potentially_missing"] == 0
        assert view["blocking_items"] == 1
        assert "secret" not in str(view)
        assert str(book) not in str(view)
    finally:
        service.close()


def test_ambiguous_live_account_is_not_authority_to_download(tmp_path, monkeypatch):
    service, book, _scope, _ = _declared_kobo(tmp_path, monkeypatch)
    try:
        # A credential unexpectedly exposing several borrowers has no one
        # exact principal for a fresh destructive acquisition decision.
        session = service._sessions[service._session_key("kobo", "vault-1")]
        from dataclasses import replace
        session._accounts.append(replace(session.accounts[0], unique_id="other"))
        view = _preview(service)
        assert view["counts"]["attention_required"] == 1
        assert view["blocking_items"] == 1
        assert book.is_file()
    finally:
        service.close()
