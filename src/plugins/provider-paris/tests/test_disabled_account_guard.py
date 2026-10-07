from __future__ import annotations

import pytest

from cifereca.plugin_api import ProviderConfigurationError
from cifereca_paris import ParisProviderSession


class DummyConfig:
    def get(self, key, default=None):
        return default


class RecordingDiagnostics:
    def __init__(self):
        self.rows = []

    def add(self, *args, **kwargs):
        self.rows.append((args, kwargs))


def _session(diagnostics=None) -> ParisProviderSession:
    return ParisProviderSession(
        credential_id="paris-default",
        label="Paris",
        credentials={"username": "u", "password": "p"},
        options={},
        config=DummyConfig(),
        diagnostics=diagnostics or RecordingDiagnostics(),
    )


@pytest.mark.parametrize(
    "summary",
    [
        {"IsDisabled": True},
        {"IsBlocked": True},
        {"IsActive": False},
        {"Status": "disabled"},
        {"State": "Désactivé"},
        {"MembershipStatus": "expired"},
    ],
)
def test_connect_rejects_explicitly_disabled_account_before_pairings(monkeypatch, summary):
    diagnostics = RecordingDiagnostics()
    session = _session(diagnostics)
    monkeypatch.setattr(session.client, "login", lambda username, password: {"success": True})
    monkeypatch.setattr(session.client, "summary", lambda provider, uid: dict(summary))
    pairings_called = []
    monkeypatch.setattr(session.client, "pairings", lambda: pairings_called.append(True) or [])

    try:
        with pytest.raises(ProviderConfigurationError) as raised:
            session.connect()

        message = str(raised.value)
        assert "compte désactivé ou inutilisable" in message
        assert "avant ADEPT" in message
        assert pairings_called == []
        assert session.connected is False
        assert session.account_status["usable"] is False
        assert any(
            args[:3] == ("AUTH", "account status", "error")
            and kwargs.get("detail", {}).get("usable") is False
            for args, kwargs in diagnostics.rows
        )
    finally:
        session.close()


def test_connect_keeps_account_when_no_explicit_disabled_signal(monkeypatch):
    session = _session()
    monkeypatch.setattr(session.client, "login", lambda username, password: {"success": True})
    monkeypatch.setattr(
        session.client,
        "summary",
        lambda provider, uid: {"DisplayName": "Lecteur", "Barcode": "123", "Status": "active"},
    )
    monkeypatch.setattr(session.client, "pairings", lambda: [])

    try:
        accounts = session.connect()

        assert len(accounts) == 1
        assert accounts[0].display_name == "Lecteur"
        assert session.connected is True
        assert session.account_status == {"usable": True, "reason": None}
        assert session.diagnostic_state()["account_status"]["usable"] is True
    finally:
        session.close()


def test_unknown_status_does_not_false_positive():
    session = _session()
    try:
        assert session._account_unusable_reason({"Status": "renewal_pending"}) is None
        assert session._account_unusable_reason({}) is None
    finally:
        session.close()
