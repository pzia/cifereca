"""P0 #5975: an ACSM's borrower is source-scoped, never Paris-only.

Synthetic, isolated SQLite and ADEPT mocks only. A legacy Paris v15 record
cannot attest another provider whose credentials and reader ID happen to match.
No ACSM GET, ADEPT fulfillment, loan POST or entitlement write.
"""
from __future__ import annotations

from dataclasses import replace

from tests.historical_adept_seed import seed_historical_adept_binding
from tests.test_core_p0_5851_generic_adept_grants_v16 import _persist_account
from tests.test_paris_borrower_adept_binding import service_with_linked_borrower


def _foreign_linked_borrower(service, loan, *, provider):
    service.storage.provider_connection_put({
        "provider_id": provider, "credential_id": loan.source_credential,
        "identity_id": "alice", "label": "Synthetic independent library",
    })
    _persist_account(
        service.storage, provider=provider,
        credential=loan.source_credential,
    )
    service.accounts.append(replace(service.accounts[1], provider_id=provider))
    return replace(loan, source_provider=provider)


def test_nonparis_linked_loan_never_inherits_unscoped_paris_v15_grant(tmp_path):
    service, paris_loan = service_with_linked_borrower(tmp_path)
    try:
        seed_historical_adept_binding(
            service, credential_id="alice-paris", account_id="linked-123",
            borrower_identity_id="bob", confirmed=True,
        )
        foreign = _foreign_linked_borrower(
            service, paris_loan, provider="biblioaccess.la-rochelle",
        )
        legacy = service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        )
        assert legacy is not None
        # Different source, same credential/account: v15 cannot authorize
        # Bob or accidentally veto a legitimate *other-provider* v16 grant.
        assert service._paris_adept_identity_for_loan(
            foreign, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
        coords = dict(
            provider_id="biblioaccess.la-rochelle",
            credential_id="alice-paris", account_id="linked-123",
        )
        service.storage.adept_borrower_grant_put(
            **coords, connection_identity_id="alice",
            borrower_identity_id="bob", profile_id="bob-profile",
            confirmed=True,
        )
        assert service._paris_adept_identity_for_loan(
            foreign, connection_identity_id="alice",
        ) == ("bob", None)
        service.storage.adept_borrower_grant_delete(**coords)
        assert service._paris_adept_identity_for_loan(
            foreign, connection_identity_id="alice",
        ) == (None, "adept_borrower_binding_required")
        assert service.storage.paris_borrower_adept_binding_get(
            credential_id="alice-paris", account_id="linked-123",
        ) == legacy
    finally:
        service.close()


def test_other_source_primary_must_match_exact_native_account(tmp_path):
    service, paris_loan = service_with_linked_borrower(tmp_path)
    try:
        service.storage.provider_connection_put({
            "provider_id": "future-lender", "credential_id": "alice-paris",
            "identity_id": "alice", "label": "Synthetic library",
        })
        service.accounts.append(replace(
            service.accounts[0], provider_id="future-lender",
        ))
        primary = replace(
            paris_loan, source_provider="future-lender",
            source_account_id="primary",
        )
        assert service._paris_adept_identity_for_loan(
            primary, connection_identity_id="alice",
        ) == ("alice", None)
        assert service._paris_adept_identity_for_loan(
            replace(primary, source_account_id="unrelated-reader"),
            connection_identity_id="alice",
        ) == (None, "adept_borrower_ambiguous")
    finally:
        service.close()


def _registered_primary_source(service, loan, *, alias):
    from types import SimpleNamespace as NS
    from cifereca.providers import ProviderDescriptor, ProviderCapabilities

    provider = "synthetic-lender"
    service.storage.provider_connection_put({
        "provider_id": provider, "credential_id": loan.source_credential,
        "identity_id": "alice",
    })
    service.accounts.append(replace(
        service.accounts[0], provider_id=provider,
    ))
    descriptor = ProviderDescriptor(
        id=provider, display_name="Synthetic", description="Test",
        state="ready", capabilities=ProviderCapabilities(loans=True),
        existing_primary_loan_account_alias=alias,
    )
    return replace(loan, source_provider=provider), NS(descriptor=descriptor)


def test_adept_primary_uses_installed_alias_not_historical_literal(tmp_path, monkeypatch):
    from unittest.mock import Mock
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        loan, plugin = _registered_primary_source(service, loan, alias="reader-main")
        monkeypatch.setattr(service.registry, "maybe_get", lambda _key: plugin)
        original = service.explicit_adept_borrower_grant_for_source
        grant_reader = Mock(wraps=original)
        monkeypatch.setattr(service, "explicit_adept_borrower_grant_for_source", grant_reader)

        assert service._paris_adept_identity_for_loan(
            replace(loan, source_account_id="reader-main"),
            connection_identity_id="alice",
        ) == ("alice", None)
        grant_reader.assert_called_once_with(
            provider_id="synthetic-lender", credential_id=loan.source_credential,
            account_id="primary", connection_identity_id="alice",
        )
        grant_reader.reset_mock()
        assert service._paris_adept_identity_for_loan(
            replace(loan, source_account_id="primary"),
            connection_identity_id="alice",
        ) == (None, "adept_borrower_ambiguous")
        grant_reader.assert_not_called()
    finally:
        service.close()


def test_adept_literal_primary_is_not_implicit_alias_when_plugin_declares_none(
    tmp_path, monkeypatch,
):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        loan, plugin = _registered_primary_source(service, loan, alias=None)
        monkeypatch.setattr(service.registry, "maybe_get", lambda _key: plugin)
        assert service._paris_adept_identity_for_loan(
            replace(loan, source_account_id="primary"),
            connection_identity_id="alice",
        ) == (None, "adept_borrower_ambiguous")
        assert service._paris_adept_identity_for_loan(
            replace(loan, source_account_id=""),
            connection_identity_id="alice",
        ) == ("alice", None)
    finally:
        service.close()


def test_adept_declared_alias_cannot_choose_a_duplicate_literal_reader(
    tmp_path, monkeypatch,
):
    service, loan = service_with_linked_borrower(tmp_path)
    try:
        loan, plugin = _registered_primary_source(service, loan, alias="reader-main")
        monkeypatch.setattr(service.registry, "maybe_get", lambda _key: plugin)
        service.accounts.append(replace(
            service.accounts[1], provider_id=loan.source_provider,
            unique_id="reader-main",
        ))
        assert service._paris_adept_identity_for_loan(
            replace(loan, source_account_id="reader-main"),
            connection_identity_id="alice",
        ) == (None, "adept_borrower_ambiguous")
    finally:
        service.close()
