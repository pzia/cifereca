"""P0 #976: borrower confirmation, linked identities and shared-loan DRM safety."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.identities import AdeptProfileResolutionError
from cifereca.models import Account, Loan
from cifereca.plugin_api import UserFacingError
from cifereca.services import LibraryService
from cifereca.storage import Storage
from cifereca.core.borrower_adept_binding import (
    borrower_key, loan_owner_key, read_proof, conflicting_loan_identities,
)


def _service(tmp_path, *, two_connections=True):
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set("identities", [
        {"id": "default", "label": "A", "directory_name": "a", "preferences": {"reading_languages": ["fr"]}},
        {"id": "alex", "label": "B", "directory_name": "b", "preferences": {"reading_languages": ["fr"]}},
    ])
    config.set("providers.accounts", [])
    lib = LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics())
    lib.storage.provider_connection_put({
        "identity_id": "default", "provider_id": "paris",
        "credential_id": "paris-a", "label": "Paris A",
    })
    if two_connections:
        lib.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "paris",
            "credential_id": "paris-b", "label": "Paris B",
        })
    lib.accounts = [
        Account(display_name="Primary A", unique_id="", is_primary=True,
                provider_id="paris", credential_id="paris-a", identity_id="default"),
        Account(display_name="Linked B from A", unique_id="linked-b", is_primary=False,
                provider_id="paris", credential_id="paris-a", identity_id="default"),
    ]
    if two_connections:
        lib.accounts += [
            Account(display_name="Primary B", unique_id="", is_primary=True,
                    provider_id="paris", credential_id="paris-b", identity_id="alex"),
            Account(display_name="Linked A from B", unique_id="linked-a", is_primary=False,
                    provider_id="paris", credential_id="paris-b", identity_id="alex"),
        ]
    lib.storage.adept_profile_binding_set("identity", "default", "adept-a")
    lib.storage.adept_profile_binding_set("identity", "alex", "adept-b")
    return lib


def _loan(credential="paris-a", account="linked-b", item="book-1", loan_id="tx-1"):
    return Loan(
        id=loan_id, provider="PNB", medium="digital", record_id=item, title="Même livre",
        source_provider="paris", source_credential=credential,
        source_account_id=account, identity_id="default" if credential == "paris-a" else "alex",
        ebook_url="https://example.invalid/token", due_at="2099-01-01T00:00:00Z",
    )


def test_missing_and_unproved_borrower_never_contacts_provider_with_two_identities(tmp_path, monkeypatch):
    lib = _service(tmp_path)
    try:
        called = []
        monkeypatch.setattr(lib, "acquire_digital_loan", lambda *a, **k: called.append(a))
        loan = _loan()
        for active in ("default", "alex"):
            lib.select_identity(active)
            result = lib._acquire_active_loan_automatically(loan)
            assert result["status"] == "attention_required"
            assert result["code"] == "adept_borrower_binding_required"
        assert called == []
        assert lib.storage.acquisitions() == []
        with pytest.raises(Exception):
            lib.confirm_adept_borrower(
                provider_id="paris", credential_id="paris-a", account_id="linked-b",
                identity_id="alex", profile_id="adept-b",
            )  # explicit confirmation mandatory
        assert read_proof(lib.storage, borrower_key("paris", "paris-a", "linked-b")) is None
    finally:
        lib.close()


def test_linked_borrower_can_belong_to_identity_other_than_login(tmp_path):
    lib = _service(tmp_path)
    try:
        lib.confirm_adept_borrower(
            provider_id="paris", credential_id="paris-a", account_id="linked-b",
            identity_id="alex", profile_id="adept-b", confirmed=True,
        )
        proof = read_proof(lib.storage, borrower_key("paris", "paris-a", "linked-b"))
        assert proof["identity_id"] == "alex" and proof["profile_id"] == "adept-b"
        lib.select_identity("default")
        # Public resolver must never pick the login's bound ADEPT profile.
        lib.kobo_profiles = lambda: [
            {"profile_id": "adept-a", "name": "reader-a", "fetch_capable": True},
            {"profile_id": "adept-b", "name": "reader-b", "fetch_capable": True},
        ]
        name, source = lib.resolve_adept_profile(
            provider_id="paris", credential_id="paris-a", account_id="linked-b",
        )
        assert name == "reader-b"
        assert source == "identity"
        with pytest.raises(AdeptProfileResolutionError):
            lib.resolve_adept_profile(
                provider_id="paris", credential_id="paris-a", account_id="primary",
            )
    finally:
        lib.close()


def test_duplicate_transaction_blocked_both_orders_until_owner_confirmed(tmp_path, monkeypatch):
    lib = _service(tmp_path)
    try:
        a = _loan("paris-a", "linked-b")
        b = _loan("paris-b", "primary")
        lib.confirm_adept_borrower(
            provider_id="paris", credential_id="paris-a", account_id="linked-b",
            identity_id="alex", profile_id="adept-b", confirmed=True,
        )
        lib.confirm_adept_borrower(
            provider_id="paris", credential_id="paris-b", account_id="primary",
            identity_id="alex", profile_id="adept-b", confirmed=True,
        )
        lib._persist_active_loans("default", [a])
        lib._persist_active_loans("alex", [b])
        identities = conflicting_loan_identities(
            lib.storage.entitlements(provider_id="paris"),
            [], provider_id="paris", loan_id="tx-1", item_id="book-1",
        )
        assert identities == {"default", "alex"}
        called = []
        monkeypatch.setattr(lib, "acquire_digital_loan", lambda loan, **kw: (
            called.append(loan.source_credential),
            (_ for _ in ()).throw(UserFacingError("test", code="temporary")),
        )[-1])
        for loan in (a, b, b, a):
            assert lib._acquire_active_loan_automatically(loan)["code"] == "adept_loan_owner_ambiguous"
        assert called == []

        # Exact loan owner must be explicitly assigned; rights/provenance are
        # kept for both connections, with only one Adobe activation path.
        lib.confirm_adept_loan_owner(a, identity_id="alex", confirmed=True)
        owner = read_proof(lib.storage, loan_owner_key("paris", "tx-1", "book-1"))
        assert owner["identity_id"] == "alex"
        # The other visible copy has the SAME ADEPT identity, but transaction
        # ownership remains scoped to exact account. This test verifies only
        # one Adobe profile is in play; a future per-connection owner refinement
        # should further narrow the physical download path.
        assert lib._acquire_active_loan_automatically(b)["status"] == "attention_required"
        assert lib._acquire_active_loan_automatically(a)["status"] == "attention_required"
        assert called  # explicitly confirmed owner may attempt provider
        recorded = next(row for row in lib.storage.acquisitions(provider_id="paris")
                        if row["credential_id"] == "paris-a")
        source_right = next(r for r in lib.storage.entitlements(provider_id="paris")
                            if r["credential_id"] == "paris-a" and r["loan_id"] == "tx-1")
        # ADEPT target identity may differ from the login identity, but
        # entitlements always belong to the exact provider/borrower source.
        assert recorded["identity_id"] == "alex"
        assert recorded["entitlement_id"] == source_right["entitlement_id"]
    finally:
        lib.close()


def test_unknown_connection_fails_closed_independently_of_current_tab(tmp_path, monkeypatch):
    lib = _service(tmp_path, two_connections=False)
    try:
        lib.select_identity("alex")
        loan = _loan(credential="removed-connection", account="primary")
        called = []
        monkeypatch.setattr(lib, "acquire_digital_loan", lambda *a, **k: called.append(1))
        result = lib._acquire_active_loan_automatically(loan)
        assert result["code"] == "adept_borrower_identity_unproven"
        assert called == []
    finally:
        lib.close()


def test_borrower_confirmation_rejects_wrong_profile_and_unknown_account(tmp_path):
    lib = _service(tmp_path)
    try:
        with pytest.raises(Exception):
            lib.confirm_adept_borrower(
                provider_id="paris", credential_id="paris-a", account_id="linked-b",
                identity_id="alex", profile_id="adept-a", confirmed=True,
            )
        with pytest.raises(Exception):
            lib.confirm_adept_borrower(
                provider_id="paris", credential_id="paris-a", account_id="not-registered",
                identity_id="alex", profile_id="adept-b", confirmed=True,
            )
        assert read_proof(lib.storage, borrower_key("paris", "paris-a", "linked-b")) is None
    finally:
        lib.close()
