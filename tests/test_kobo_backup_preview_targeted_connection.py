"""A Kobo preview must never connect unrelated providers or accounts."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.core import provider_sessions
from cifereca.providers import ProviderConfigurationError


class FakeSession:
    provider_id = "kobo"
    connected = False
    capabilities = SimpleNamespace(backup=True)

    def __init__(self, credential_id, calls):
        self.credential_id = credential_id
        self.calls = calls

    def connect(self):
        self.calls.append(("connect", self.credential_id))
        self.connected = True
        return []

    def close(self):
        self.calls.append(("close", self.credential_id))
        self.connected = False


def _service(rows, *, identity="owner"):
    calls = []
    session_by_credential = {}

    def make_session(**kw):
        calls.append(("create", kw["credential_id"]))
        session = FakeSession(kw["credential_id"], calls)
        session_by_credential[kw["credential_id"]] = session
        return session

    plugin = SimpleNamespace(
        descriptor=SimpleNamespace(state="ready", display_name="Kobo"),
        create_session=make_session,
    )
    service = SimpleNamespace(
        storage=SimpleNamespace(
            identity_for_connection=lambda provider, credential_id:
                identity if provider == "kobo" and credential_id == "vault-1" else None
        ),
        config=SimpleNamespace(provider_accounts=lambda enabled_only=False: rows),
        registry=SimpleNamespace(maybe_get=lambda provider: plugin if provider == "kobo" else None),
        _session_key=lambda provider, credential_id: f"{provider}:{credential_id}",
        _sessions={},
        diagnostics=object(),
    )
    return service, calls, session_by_credential


def test_explicit_preview_connects_only_targeted_kobo_not_paris_or_other_kobo():
    rows = [
        {"provider": "paris", "id": "paris-1", "credentials": {"password": "never"}},
        {"provider": "kobo", "id": "vault-2", "credentials": {"token": "never"}},
        {"provider": "kobo", "id": "vault-1", "credentials": {"token": "target"}},
    ]
    service, calls, sessions = _service(rows)
    target = provider_sessions.connect_kobo_preview(service, "vault-1")
    assert target is sessions["vault-1"]
    assert calls == [("create", "vault-1"), ("connect", "vault-1")]
    assert set(service._sessions) == {"kobo:vault-1"}
    assert provider_sessions.connect_kobo_preview(service, "vault-1") is target
    assert calls == [("create", "vault-1"), ("connect", "vault-1")]


@pytest.mark.parametrize("credential", ["", "vault-2", "paris-1", "missing"])
def test_preview_refuses_unconfigured_or_other_identity_without_provider_traffic(credential):
    service, calls, _ = _service([
        {"provider": "kobo", "id": "vault-1"},
        {"provider": "kobo", "id": "vault-2"},
        {"provider": "paris", "id": "paris-1"},
    ])
    with pytest.raises(ProviderConfigurationError):
        provider_sessions.connect_kobo_preview(service, credential)
    assert calls == []
    assert service._sessions == {}


def test_preview_refuses_disabled_kobo_without_provider_traffic():
    service, calls, _ = _service([])
    with pytest.raises(ProviderConfigurationError, match="désactivé"):
        provider_sessions.connect_kobo_preview(service, "vault-1")
    assert calls == []
