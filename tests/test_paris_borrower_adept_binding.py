"""P0 #976: explicit Paris linked-borrower ADEPT provenance and persistence."""
from __future__ import annotations

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, Loan
from cifereca.services import LibraryService
from cifereca.storage import Storage


def service_with_linked_borrower(tmp_path):
    config = Config(tmp_path / "config.json")
    config.set("identities", [
        {"id": "alice", "label": "Alice", "directory_name": "alice"},
        {"id": "bob", "label": "Bob", "directory_name": "bob"},
    ])
    config.set("providers.accounts", [])
    service = LibraryService(config, Storage(tmp_path / "data.sqlite3"), Diagnostics())
    service.storage.provider_connection_put({
        "identity_id": "alice", "provider_id": "paris",
        "credential_id": "alice-paris", "label": "Paris Alice",
    })
    service.storage.adept_profile_binding_set("identity", "alice", "alice-profile")
    service.storage.adept_profile_binding_set("identity", "bob", "bob-profile")
    service.accounts = [
        Account(unique_id="", is_primary=True, provider_id="paris",
                credential_id="alice-paris", identity_id="alice"),
        Account(unique_id="linked-123", is_primary=False, provider_id="paris",
                credential_id="alice-paris", identity_id="alice"),
    ]
    loan = Loan(
        id="shared-loan", provider="PNB", medium="digital", record_id="book",
        title="Emprunt lié", source_provider="paris",
        source_credential="alice-paris", source_account_id="linked-123",
        identity_id="alice",
    )
    return service, loan


def test_linked_borrower_requires_explicit_adept_binding(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
        with pytest.raises(ValueError, match="explicit"):
            service.bind_paris_borrower_adept(
                credential_id="alice-paris", account_id="linked-123",
                borrower_identity_id="bob",
            )
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123"
        ) is None
    finally:
        service.close()


def test_explicit_binding_selects_borrower_not_connection_identity(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        saved = service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        assert saved["borrower_identity_id"] == "bob"
        assert saved["profile_id"] == "bob-profile"
        assert saved["confirmed_by"] == "explicit"
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == ("bob", None)
        service.current_identity = service.identity_by_id("alice")
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == ("bob", None)
    finally:
        service.close()


def test_binding_survives_storage_reopen_and_is_revocable(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    path = service.storage.path
    try:
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
    finally:
        service.close()
    reopened = Storage(path)
    try:
        assert reopened.schema_version() == 14
        assert reopened.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123"
        )["profile_id"] == "bob-profile"
        reopened.paris_borrower_adept_binding_delete(
            credential_id="alice-paris", account_id="linked-123"
        )
        assert reopened.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123"
        ) is None
    finally:
        reopened.close()


def test_profile_change_invalidates_explicit_borrower_confirmation(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        service.storage.adept_profile_binding_set("identity", "bob", "bob-profile-new")
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice",
        ) == (None, "adept_borrower_profile_changed")
        with pytest.raises(ValueError, match="not proven"):
            service.storage.paris_borrower_adept_binding_put(
                credential_id="alice-paris", account_id="linked-123",
                borrower_identity_id="bob", profile_id="bob-profile",
                confirmed=True,
            )
    finally:
        service.close()


def test_second_connection_same_linked_borrower_needs_its_own_grant(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.storage.provider_connection_put({
            "identity_id": "bob", "provider_id": "paris",
            "credential_id": "bob-paris", "label": "Paris Bob",
        })
        service.accounts.append(Account(
            unique_id="linked-123", is_primary=False,
            provider_id="paris", credential_id="bob-paris", identity_id="bob",
        ))
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        from dataclasses import replace
        second_loan = replace(
            loan, source_credential="bob-paris", identity_id="bob",
        )
        assert service._paris_adept_identity_for_loan(
            second_loan, connection_identity_id="bob",
        ) == (None, "adept_borrower_binding_required")
        service.bind_paris_borrower_adept(
            credential_id="bob-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        assert service._paris_adept_identity_for_loan(
            second_loan, connection_identity_id="bob",
        ) == ("bob", None)
    finally:
        service.close()


def test_auto_acquisition_rejects_linked_borrower_before_provider_call(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        def forbidden(*args, **kwargs):
            raise AssertionError("provider/ADEPT must not run without confirmed grant")
        service.acquire_digital_loan = forbidden
        result = service._acquire_active_loan_automatically_guarded(loan)
        assert result["status"] == "attention_required"
        assert result["code"] == "adept_borrower_binding_required"
        assert service.storage.acquisitions(provider_id="paris") == []
    finally:
        service.close()


def test_primary_borrower_keeps_proven_connection_profile_without_grant(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        from dataclasses import replace
        primary = replace(loan, source_account_id="primary")
        assert service._paris_adept_identity_for_loan(
            primary, connection_identity_id="alice",
        ) == ("alice", None)
    finally:
        service.close()


def test_connection_identity_transfer_revokes_effective_borrower_grant(tmp_path):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        saved = service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        assert saved["connection_identity_id"] == "alice"
        service.storage.provider_connection_put({
            "identity_id": "bob", "provider_id": "paris",
            "credential_id": "alice-paris", "label": "Transférée à Bob",
        })
        service.accounts[0].identity_id = "bob"
        service.accounts[1].identity_id = "bob"
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="bob",
        ) == (None, "adept_borrower_connection_changed")
    finally:
        service.close()


@pytest.mark.parametrize("account_id", ["linked-123", "primary", ""])
def test_acsm_fulfillment_uses_borrower_profile_and_persists_return_binding(tmp_path, monkeypatch, account_id):
    """The Adobe-origin profile must belong to the borrower, not the connector."""
    from pathlib import Path
    from cifereca.acquisition import Acquisition

    service, loan = service_with_linked_borrower(tmp_path)
    try:
        from dataclasses import replace
        loan = replace(loan, source_account_id=account_id)
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id=account_id,
            borrower_identity_id="bob", confirmed=True,
        )
        service._persist_active_loans("alice", [loan])
        acsm = tmp_path / "linked.acsm"
        acsm.write_text(
            "<fulfillmentToken><format>application/pdf</format></fulfillmentToken>",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            service, "acquire_digital_loan",
            lambda _loan: Acquisition(
                provider_id="paris", credential_id="alice-paris",
                account_id=account_id, item_id="book", loan_id=loan.id,
                kind="acsm", source_url="https://example.invalid/acsm",
                local_path=acsm, mime_type="application/vnd.adobe.adept+xml",
                drm="adept", acsm_path=acsm, target_dir=tmp_path,
            ),
        )
        monkeypatch.setattr(service, "kobo_profiles", lambda: [{
            "profile_id": "bob-profile", "name": "bob-cli",
            "fetch_capable": True, "provisioning": {"acsm_fetch_ready": True},
        }])
        monkeypatch.setattr(service, "runtime_diagnostic", lambda *_: {})
        captured = {}

        def execute(request):
            captured.update(request)
            destination = Path(request["destination"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"%PDF-1.7\\n1 0 obj\\n<<>>\\nendobj\\n%%EOF\\n")
            return {
                "ok": True, "profile": "bob-cli", "loan_id": "adept-bob-loan",
                "artifact": {"path": str(destination), "format": "pdf", "drm": "none"},
            }

        monkeypatch.setattr(service, "execute_acquisition_request", execute)
        result = service._acquire_active_loan_automatically(loan)
        assert result["status"] == "complete"
        assert captured["profile"] == "bob-cli"
        row = next(
            row for row in service.storage.acquisitions(status="complete")
            if row["item_id"] == "book"
        )
        assert row["metadata"]["adept_binding"]["adept_profile_id"] == "bob-profile"
        assert row["metadata"]["adept_binding"]["adept_profile_name"] == "bob-cli"
    finally:
        service.close()


def test_primary_exception_requires_confirmation_and_preserves_normal_profile(tmp_path):
    from dataclasses import replace
    service, loan = service_with_linked_borrower(tmp_path)
    loan = replace(loan, source_account_id="primary")
    try:
        with pytest.raises(ValueError, match="explicit"):
            service.bind_paris_borrower_adept(
                credential_id="alice-paris", account_id="primary",
                borrower_identity_id="bob",
            )
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="",
            borrower_identity_id="bob", confirmed=True,
        )
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice"
        ) == ("bob", None)
        assert service.storage.adept_profile_binding_get("identity", "alice") == "alice-profile"
        service.storage.adept_profile_binding_set("identity", "bob", "changed")
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice"
        ) == (None, "adept_borrower_profile_changed")
        service.storage.paris_borrower_adept_binding_delete(
            credential_id="alice-paris", account_id="primary"
        )
        assert service._paris_adept_identity_for_loan(
            loan, connection_identity_id="alice"
        ) == ("alice", None)
    finally:
        service.close()


def test_primary_exception_is_scoped_to_connection_and_rejects_ambiguity(tmp_path):
    from dataclasses import replace
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        service.bind_paris_borrower_adept(
            credential_id="alice-paris", account_id="primary",
            borrower_identity_id="bob", confirmed=True,
        )
        service.storage.provider_connection_put({
            "identity_id": "bob", "provider_id": "paris", "credential_id": "bob-paris",
        })
        service.accounts.append(Account(
            unique_id="", is_primary=True, provider_id="paris",
            credential_id="bob-paris", identity_id="bob",
        ))
        second = replace(loan, source_account_id="primary", source_credential="bob-paris")
        assert service._paris_adept_identity_for_loan(
            second, connection_identity_id="bob"
        ) == ("bob", None)
        service.accounts.append(replace(service.accounts[0]))
        with pytest.raises(ValueError, match="not uniquely identified"):
            service.bind_paris_borrower_adept(
                credential_id="alice-paris", account_id="primary",
                borrower_identity_id="bob", confirmed=True,
            )
        assert service._paris_adept_identity_for_loan(
            replace(loan, source_account_id="primary"), connection_identity_id="alice"
        ) == (None, "adept_borrower_ambiguous")
    finally:
        service.close()
