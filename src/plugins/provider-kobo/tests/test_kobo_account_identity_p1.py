"""P1 #1346: a verified Kobo account is mandatory for per-account sync.

All upstream account/status data and credentials here are synthetic. Tests do
not use network, SQLite, DRM or an acquisition backend.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import ProviderError
from cifereca_kobo import DESCRIPTOR, KoboSession


class FakeVault:
    base_url = "https://vault.example"

    def __init__(self, account):
        self.account_data = account
        self.calls = []

    def status(self, *, authenticated=False):
        assert not authenticated
        self.calls.append("status")
        return {
            "api_version": "2.3",
            "capabilities": ["library", "acquisition"],
            "acquisition_access": "configured",
        }

    def account(self):
        self.calls.append("account")
        return self.account_data

    def acquisition(self, *_args, **_kwargs):
        raise AssertionError("Connecting must never resolve an acquisition")

    def close(self):
        pass


def session(account):
    result = KoboSession(
        descriptor=DESCRIPTOR, credential_id="PRIVATE_CREDENTIAL", label="Kobo",
        credentials={"api_token": "Bearer PRIVATE_TOKEN"},
        options={"base_url": "https://vault.example"},
        config={}, storage={},
        diagnostics=SimpleNamespace(add=lambda *_a, **_kw: None),
        runtime_capabilities=SimpleNamespace(acquisition=lambda *_a: None),
    )
    result.client.close()
    fake = FakeVault(account)
    result.client = fake
    return result, fake


@pytest.mark.parametrize("account", [
    None, [], {}, {"display_name": "PRIVATE_ACCOUNT"}, {"id": None},
    {"id": ""}, {"id": 0}, {"id": False}, {"id": {}},
    {"id": ["PRIVATE_ACCOUNT"]},
    {"id": "ac-1", "account_id": "ac-2"},
    {"id": "ac-1", "account_id": 123},
    {"id": False, "account_id": "ac-1"},
    {"id": "a" * 257}, {"id": "ac 1"},
    {"id": "ac\nBearer PRIVATE_TOKEN"},
    {"id": "ac\rPRIVATE_TOKEN"}, {"id": "ac\u202ePRIVATE_SPOOF"},
    {"id": "ac\u2066PRIVATE_ISOLATE"},
])
def test_account_missing_or_ambiguous_fails_without_authentication_or_403_reset(account):
    provider, fake = session(account)
    provider._cifereca_delegation_forbidden = True
    provider._cifereca_delegation_attempts_saved = 12
    try:
        with pytest.raises(ProviderError) as exc:
            provider.connect()
        assert "identité du compte absente ou ambiguë" in str(exc.value)
        assert provider.connected is False
        assert provider.accounts == []
        assert provider._cifereca_delegation_forbidden is True
        assert provider._cifereca_delegation_attempts_saved == 12
        assert fake.calls == ["status", "account"]
        for secret in ("PRIVATE_TOKEN", "PRIVATE_ACCOUNT", "PRIVATE_SPOOF", "ac-1"):
            assert secret not in str(exc.value)
    finally:
        provider.close()


@pytest.mark.parametrize("account,expected", [
    ({"id": "ac-1"}, "ac-1"),
    ({"account_id": "ac-2"}, "ac-2"),
    ({"id": "ac-3", "account_id": "ac-3"}, "ac-3"),
    ({"id": "", "account_id": "ac-4"}, "ac-4"),
    ({"id": "urn:uuid:abcd-1234"}, "urn:uuid:abcd-1234"),
])
def test_connected_account_is_strictly_the_attested_opaque_vault_identifier(account, expected):
    provider, fake = session(account)
    provider._cifereca_delegation_forbidden = True
    provider._cifereca_delegation_attempts_saved = 12
    try:
        rows = provider.connect()
        assert len(rows) == 1
        assert rows[0].unique_id == expected
        assert provider.connected is True
        assert provider._cifereca_delegation_forbidden is False
        assert provider._cifereca_delegation_attempts_saved == 0
        assert fake.calls == ["status", "account"]
    finally:
        provider.close()


@pytest.mark.parametrize("unsafe_display", [
    {"PRIVATE": "object"}, ["PRIVATE"], "PRIVATE\nBearer SECRET",
    "PRIVATE\rSECRET", "PRIVATE\u202eSECRET",
    "x" * 121,
])
def test_untrusted_vault_account_label_never_displays_raw_control_or_object(unsafe_display):
    provider, _fake = session({
        "id": "account-1", "display_name": unsafe_display,
        "name": "Kobo Reader",
    })
    try:
        assert provider.connect()[0].display_name == "Kobo Reader"
    finally:
        provider.close()


def test_reconnect_cannot_replace_existing_account_with_unknown_fallback():
    provider, fake = session({"id": "original-reader"})
    try:
        assert provider.connect()[0].unique_id == "original-reader"
        provider._cifereca_delegation_forbidden = True
        provider._cifereca_delegation_attempts_saved = 4
        fake.account_data = {"email_hint": "PRIVATE_NAME"}
        with pytest.raises(ProviderError):
            provider.connect()
        assert provider.accounts[0].unique_id == "original-reader"
        assert provider._cifereca_delegation_forbidden is True
        assert provider._cifereca_delegation_attempts_saved == 4
    finally:
        provider.close()


def test_existing_credential_cannot_be_rebound_to_a_different_authenticated_account():
    provider, fake = session({"id": "reader-alice"})
    try:
        assert provider.connect()[0].unique_id == "reader-alice"
        provider._cifereca_delegation_forbidden = True
        provider._cifereca_delegation_attempts_saved = 9
        fake.account_data = {
            "id": "reader-bob",
            "display_name": "PRIVATE_OTHER_READER",
        }
        with pytest.raises(ProviderError) as exc:
            provider.connect()
        assert "reconfiguration explicite" in str(exc.value)
        assert "reader-alice" not in str(exc.value)
        assert "reader-bob" not in str(exc.value)
        assert provider.accounts[0].unique_id == "reader-alice"
        assert provider._cifereca_delegation_forbidden is True
        assert provider._cifereca_delegation_attempts_saved == 9
        assert fake.calls == ["status", "account", "status", "account"]
    finally:
        provider.close()


def test_reconnect_same_account_after_scope_reauthorization_is_allowed():
    provider, fake = session({"id": "reader-alice", "display_name": "My Kobo"})
    try:
        provider.connect()
        provider._cifereca_delegation_forbidden = True
        provider._cifereca_delegation_attempts_saved = 4
        fake.account_data = {"account_id": "reader-alice", "name": "My Kobo"}
        assert provider.connect()[0].unique_id == "reader-alice"
        assert provider._cifereca_delegation_forbidden is False
        assert provider._cifereca_delegation_attempts_saved == 0
        assert len(provider.accounts) == 1
    finally:
        provider.close()
